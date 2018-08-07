"""Algorithm that creates an averaged Dst.

"""
from __future__ import absolute_import

from .Algorithm import Algorithm
from .AlgorithmException import AlgorithmException
from ..ObservatoryMetadata import ObservatoryMetadata
import numpy
import obspy.core


# Possible correction factors.
# Defaults to 1.0 if station not found in list.
CORR = {
    'HON': 1.0,
    'SJG': 1.0,
    'HER': 1.0,
    'KAK': 1.0,
    'GUA': 1.0
}


class AverageAlgorithm(Algorithm):
    """Algorithm that creates an averaged Dst.

    Parameters
    ----------

    """

    def __init__(self, observatories=None, channel=None, scales=[None, ]):
        Algorithm.__init__(self)
        self._npts = -1
        self._stt = -1
        self._stats = None
        self.scales = scales
        self.observatories = observatories
        self.outchannel = channel
        self.observatoryMetadata = ObservatoryMetadata()

    def check_stream(self, timeseries):
        """checks a stream to make certain the required data
            exists.

        Parameters
        ----------
        timeseries: obspy.core.Stream
            stream to be checked.
        """

        # A stream produced by EdgeFactory should always pass these checks.

        # must have only one channel for each observatory
        if len(timeseries) != len(self.observatories):
            raise AlgorithmException(
                'Expected data for %d stations, received %d \n'
                    'Only 1 channel may be averaged at one time'
                    % (len(self.observatories), len(timeseries)))

        first = True
        # timeseries starttime and number of samples must match
        for ts in timeseries:
            # grab 1st set of stats to use in output.
            # Its values will be good if checks pass.
            if first:
                self._stats = ts.stats
                self._npts = ts.stats.npts
                self._stt = ts.stats.starttime
                first = False

            if ts.stats.npts != self._npts:
                raise AlgorithmException(
                    'Received timeseries have different lengths')

            if numpy.isnan(ts.data).all():
                raise AlgorithmException(
                    'Trace for %s observatory is completely empty.'
                    % (ts.stats.station))

            if ts.stats.starttime != self._stt:
                raise AlgorithmException(
                    'Received timeseries have different starttimes')

    def process(self, timeseries):
        """averages a channel across multiple stations

        Parameters
        ----------

        Returns
        -------
        out_stream:
            new stream object containing the averaged values.
        """

        # If outchannel is not initialized it defaults to the
        # input channel of the timeseries
        if not self.outchannel:
            self.outchannel = timeseries[0].stats.channel

        if not self.observatories:
            self.observatories = []
            for trace in timeseries:
                self.observatories += [trace.stats.station, ]

        # Set Correction values if specified and add a dicitonary
        # if observatory is not already set in CORR
        if self.scales[0]:
            for obs in self.observatories:
                if obs not in CORR:
                    new_obs = {str(obs): 1.0}
                    CORR.update(new_obs)
            for (i, obs) in enumerate(self.observatories):
                CORR[obs] = self.scales[i]

        # Run checks on input timeseries
        self.check_stream(timeseries)

        # initialize array for data to be appended
        combined = []
        # loop over stations
        for obsy in self.observatories:

            # lookup latitude correction factor, default = 1.0
            latcorr = 1.0
            if obsy in CORR:
                latcorr = CORR[obsy]

            # create array of data for each station
            # and take into account correction factor
            ts = timeseries.select(station=obsy)[0]
            combined.append(ts.data * latcorr)

        # after looping over stations, compute average
        dst_tot = numpy.mean(combined, axis=0)

        # Create a stream from the trace function
        stream = obspy.core.Stream((
                get_trace(self.outchannel, self._stats, dst_tot), ))

        # return averaged values as a stream
        return stream

    @classmethod
    def add_arguments(cls, parser):
        """Add command line arguments to argparse parser.

        Parameters
        ----------
        parser: ArgumentParser
            command line argument parser
        """
        parser.add_argument('--average-observatory-scale',
               default=(None,),
               help='Scale factor for observatories specified with ' +
                    '--observatory argument',
               nargs='*',
               type=float)

    def configure(self, arguments):
        """Configure algorithm using comand line arguments.

        Parameters
        ----------
        arguments: Namespace
            parsed command line arguments
        """

        self.observatories = arguments.observatory
        if arguments.outchannels:
            if len(arguments.outchannels) > 1:
                raise AlgorithmException(
                    'Only 1 channel can be specified')
            self.outchannel = arguments.outchannels[0]

        self.scales = arguments.average_observatory_scale
        if self.scales[0] is not None:
            if len(self.observatories) != len(self.scales):
                raise AlgorithmException(
                    'Mismatch between observatories and scale factors')
            else:
                for (i, obs) in enumerate(self.observatories):
                    CORR[obs] = self.scales[i]


def get_trace(channel, stats, data):
    """Utility to create a new trace object.

    Parameters
    ----------
    channel : str
        channel name.
    stats : obspy.core.Stats
        channel metadata to clone.
    data : numpy.array
        channel data.

    Returns
    -------
    obspy.core.Trace
        trace containing data and metadata.
    """
    New_stats = obspy.core.Stats(stats)

    if 'data_interval' in stats:
        New_stats.data_interval = stats.data_interval
    elif stats.delta == 60:
        New_stats.data_interval = 'minute'
    elif stats.delta == 1:
        New_stats.data_interval = 'second'

    New_stats.channel = channel
    New_stats.station = 'USGS'
    New_stats.network = 'NT'
    New_stats.location = stats.location

    return obspy.core.Trace(data, New_stats)
