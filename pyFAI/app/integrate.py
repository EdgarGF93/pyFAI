#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Project: Fast Azimuthal integration
#             https://github.com/silx-kit/pyFAI
#
#
#    Copyright (C) 2013-2018 European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Jérôme Kieffer (Jerome.Kieffer@ESRF.eu)
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  .
#  The above copyright notice and this permission notice shall be included in
#  all copies or substantial portions of the Software.
#  .
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#  THE SOFTWARE.


"""GUI tool for configuring azimuthal integration on series of files."""

__author__ = "Jerome Kieffer"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "MIT"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "04/02/2019"
__satus__ = "production"

import sys
import logging
import time
import numpy
import os.path
import six

import fabio

logging.basicConfig(level=logging.INFO)
logging.captureWarnings(True)
logger = logging.getLogger("pyFAI")

import pyFAI.utils
import pyFAI.worker
from pyFAI.io import DefaultAiWriter
from pyFAI.io import HDF5Writer
from pyFAI.utils.shell import ProgressBar
from pyFAI import average

from argparse import ArgumentParser

try:
    from rfoo.utils import rconsole
    rconsole.spawn_server()
    logger.debug("Socket opened for debugging using rfoo")
except ImportError:
    logger.debug("No socket opened for debugging -> please install rfoo")


def integrate_gui(options, args):
    from silx.gui import qt
    from pyFAI.gui.IntegrationDialog import IntegrationDialog
    from pyFAI.gui.IntegrationDialog import IntegrationProcess

    app = qt.QApplication([])

    from pyFAI.gui.ApplicationContext import ApplicationContext
    settings = qt.QSettings(qt.QSettings.IniFormat,
                            qt.QSettings.UserScope,
                            "pyfai",
                            "pyfai-integrate",
                            None)
    context = ApplicationContext(settings)

    def moveCenterTo(window, center):
        half = window.size() * 0.5
        half = qt.QPoint(half.width(), half.height())
        corner = center - half
        window.move(corner)

    def processData():
        center = window.geometry().center()
        window.setVisible(False)
        window.deleteLater()
        input_data = window.input_data
        if input_data is None or len(input_data) == 0:
            dialog = qt.QFileDialog(directory=os.getcwd())
            dialog.setWindowTitle("Select images to integrate")

            from pyFAI.gui.utils import FilterBuilder
            builder = FilterBuilder.FilterBuilder()
            builder.addImageFormat("EDF image files", "edf")
            builder.addImageFormat("TIFF image files", "tif tiff")
            builder.addImageFormat("NumPy binary files", "npy")
            builder.addImageFormat("CBF files", "cbf")
            builder.addImageFormat("MarCCD image files", "mccd")
            dialog.setNameFilters(builder.getFilters())

            dialog.setFileMode(qt.QFileDialog.ExistingFiles)
            moveCenterTo(dialog, center)
            result = dialog.exec_()
            if not result:
                return
            input_data = [str(i) for i in dialog.selectedFiles()]
            center = dialog.geometry().center()
            dialog.close()

        config = window.get_config()

        dialog = IntegrationProcess(None)
        dialog.adjustSize()
        moveCenterTo(dialog, center)

        class QtProcess(qt.QThread):
            def run(self):
                observer = dialog.createObserver(qtSafe=True)
                process(input_data, window.output_path, config, options.monitor_key, observer)

        qtProcess = QtProcess()
        qtProcess.start()

        result = dialog.exec_()
        if result:
            qt.QMessageBox.information(dialog,
                                       "Integration",
                                       "Batch processing completed.")
        else:
            qt.QMessageBox.information(dialog,
                                       "Integration",
                                       "Batch processing interrupted.")
        dialog.deleteLater()

    window = IntegrationDialog(args, options.output, json_file=options.json, context=context)
    window.batchProcessRequested.connect(processData)
    window.show()

    result = app.exec_()
    context.saveSettings()
    return result


def get_monitor_value(image, monitor_key):
    """Return the monitor value from an image using an header key.

    :param fabio.fabioimage.FabioImage image: Image containing the header
    :param str monitor_key: Key containing the monitor
    :return: returns the monitor else returns 1.0
    :rtype: float
    """
    if monitor_key is None or monitor_key == "":
        return 1.0
    try:
        monitor = average.get_monitor_value(image, monitor_key)
        return monitor
    except average.MonitorNotFound:
        logger.warning("Monitor %s not found. No normalization applied.", monitor_key)
        return 1.0
    except Exception as e:
        logger.warning("Fail to load monitor. No normalization applied. %s", str(e))
        return 1.0


class IntegrationObserver(object):
    """Interface providing access to the to the processing of the `process`
    function."""

    def __init__(self):
        self.__is_interruption_requested = False

    def is_interruption_requested(self):
        return self.__is_interruption_requested

    def request_interruption(self):
        self.__is_interruption_requested = True

    def worker_initialized(self, worker):
        """
        Called when the worker is initialized

        :param int data_count: Number of data to integrate
        """
        pass

    def processing_started(self, data_count):
        """
        Called before starting the full processing.

        :param int data_count: Number of data to integrate
        """
        pass

    def processing_data(self, data_id, filename):
        """
        Start processing the data `data_id`

        :param int data_id: Id of the data
        :param str filename: Filename of the data, if any.
        """
        pass

    def data_result(self, data_id, result):
        """
        Called after each data processing, with the result

        :param int data_id: Id of the data
        :param object result: Result of the integration.
        """
        pass

    def processing_interrupted(self, reason=None):
        """Called before `processing_finished` if the processing was
        interrupted.

        :param [str,Exception,None] error: A reason of the interruption.
        """
        pass

    def processing_succeeded(self):
        """Called before `processing_finished` if the processing succedded."""
        pass

    def processing_finished(self):
        """Called when the full processing is finisehd (interupted or not)."""
        pass


class ShellIntegrationObserver(IntegrationObserver):
    """
    Implement `IntegrationObserver` as a shell display.
    """

    def __init__(self):
        super(ShellIntegrationObserver, self).__init__()
        self._progress_bar = None

    def processing_started(self, data_count):
        self._progress_bar = ProgressBar("Integration", data_count, 20)

    def processing_data(self, data_id, filename=None):
        if filename:
            if len(filename) > 100:
                message = os.path.basename(filename)
            else:
                message = filename
        else:
            message = ""
        self._progress_bar.update(data_id + 1, message=message)

    def processing_finished(self):
        self._progress_bar.clear()


def process(input_data, output, config, monitor_name, observer):
    """
    Integrate a set of data.

    :param List[str] input_data: List of input filenames
    :param str output: Filename of directory output
    :param dict config: Dictionary to configure `pyFAI.worker.Worker`
    :param IntegrationObserver observer: Observer of the processing
    :param:
    """
    worker = pyFAI.worker.Worker()
    worker_config = config.copy()

    json_monitor_name = worker_config.pop("monitor_name", None)
    if monitor_name is None:
        monitor_name = json_monitor_name
    elif json_monitor_name is not None:
        logger.warning("Monitor name from command line argument override the one from the configuration file.")
    worker.set_config(worker_config, consume_keys=True)
    worker.output = "raw"

    # Check unused keys
    for key in worker_config.keys():
        # FIXME this should be read also
        if key in ["application", "version"]:
            continue
        logger.warning("Configuration key '%s' from json is unused", key)

    worker.safe = False  # all processing are expected to be the same.
    start_time = time.time()

    if observer is not None:
        observer.worker_initialized(worker)

    # Skip invalide data
    valid_data = []
    for item in input_data:
        if isinstance(item, six.string_types):
            if os.path.isfile(item):
                valid_data.append(item)
            else:
                if "::" in item:
                    try:
                        fabio.open(item)
                        valid_data.append(item)
                    except Exception:
                        logger.warning("File %s do not exists. File ignored.", item)
                else:
                    logger.warning("File %s do not exists. File ignored.", item)
        elif isinstance(item, fabio.fabioimage.FabioImage):
            valid_data.append(item)
        elif isinstance(item, numpy.ndarray):
            valid_data.append(item)
        else:
            logger.warning("Type %s unsopported. Data ignored.", item)

    if observer is not None:
        observer.processing_started(len(valid_data))

    # Integrate files one by one
    for iitem, item in enumerate(valid_data):
        logger.debug("Processing %s", item)

        # TODO rework it as source
        if isinstance(item, six.string_types):
            kind = "filename"
            fabio_image = fabio.open(item)
            filename = fabio_image.filename
            multiframe = fabio_image.nframes > 1
        elif isinstance(item, fabio.fabioimage.FabioImage):
            kind = "fabio-image"
            fabio_image = item
            multiframe = fabio_image.nframes > 1
            filename = fabio_image.filename
        elif isinstance(item, numpy.ndarray):
            kind = "numpy-array"
            filename = None
            fabio_image = None
            multiframe = False

        if observer is not None:
            observer.processing_data(iitem + 1, filename=filename)

        if filename:
            output_name = os.path.splitext(filename)[0]
        else:
            output_name = "array_%d" % iitem

        if multiframe:
            extension = "_pyFAI.h5"
        else:
            if worker.do_2D():
                extension = ".azim"
            else:
                extension = ".dat"
        output_name = "%s%s" % (output_name, extension)

        if output:
            if os.path.isdir(output):
                basename = os.path.basename(output_name)
                outpath = os.path.join(output, basename)
            else:
                outpath = os.path.abspath(output)
        else:
            outpath = output_name

        if fabio_image is None:
            if item.ndim == 3:
                writer = HDF5Writer(outpath)
                writer.init(fai_cfg=config)
                for iframe, data in enumerate(item):
                    result = worker.process(data=data,
                                            writer=writer)
                    if observer is not None:
                        if observer.is_interruption_requested():
                            break
                        observer.data_result(iitem, result)
            else:
                data = item
                writer = DefaultAiWriter(outpath, worker.ai)
                result = worker.process(data=data,
                                        writer=writer)
                if observer is not None:
                    observer.data_result(iitem, result)
        else:
            if multiframe:
                writer = HDF5Writer(outpath, append_frames=True)
                writer.init(fai_cfg=config)

                for iframe in range(fabio_image.nframes):
                    fimg = fabio_image.getframe(iframe)
                    normalization_factor = get_monitor_value(fimg, monitor_name)
                    data = fimg.data
                    result = worker.process(data=data,
                                            metadata=fimg.header,
                                            normalization_factor=normalization_factor,
                                            writer=writer)
                    if observer is not None:
                        if observer.is_interruption_requested():
                            break
                        observer.data_result(iitem, result)
                writer.close()
            else:
                writer = DefaultAiWriter(outpath, worker.ai)

                normalization_factor = get_monitor_value(fabio_image, monitor_name)
                data = fabio_image.data
                result = worker.process(data,
                                        normalization_factor=normalization_factor,
                                        writer=writer)
                if observer is not None:
                    observer.data_result(iitem, result)
                writer.close()

        if observer is not None:
            if observer.is_interruption_requested():
                break

    if observer is not None:
        if observer.is_interruption_requested():
            logger.info("Processing was aborted")
            observer.processing_interrupted()
        else:
            observer.processing_succeeded()
        observer.processing_finished()
    logger.info("Processing done in %.3fs !", (time.time() - start_time))
    return 0


def integrate_shell(options, args):
    import json
    with open(options.json) as f:
        config = json.load(f)

    observer = ShellIntegrationObserver()
    monitor_name = options.monitor_key
    filenames = args
    output = options.output
    return process(filenames, output, config, monitor_name, observer)


def _main(args):
    """Execute the application

    :param str args: Command line argument without the program name
    :rtype: int
    """
    usage = "pyFAI-integrate [options] file1.edf file2.edf ..."
    version = "pyFAI-integrate version %s from %s" % (pyFAI.version, pyFAI.date)
    description = """
    PyFAI-integrate is a graphical interface (based on Python/Qt4) to perform azimuthal integration
on a set of files. It exposes most of the important options available within pyFAI and allows you
to select a GPU (or an openCL platform) to perform the calculation on."""
    epilog = """PyFAI-integrate saves all parameters in a .azimint.json (hidden) file. This JSON file
is an ascii file which can be edited and used to configure online data analysis using
the LImA plugin of pyFAI.

Nota: there is bug in debian6 making the GUI crash (to be fixed inside pyqt)
http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=697348"""
    parser = ArgumentParser(usage=usage, description=description, epilog=epilog)
    parser.add_argument("-V", "--version", action='version', version=version)
    parser.add_argument("-v", "--verbose",
                        action="store_true", dest="verbose", default=False,
                        help="switch to verbose/debug mode")
    parser.add_argument('--debug',
                        dest="debug",
                        action="store_true",
                        default=False,
                        help='Set logging system in debug mode')
    parser.add_argument("-o", "--output",
                        dest="output", default=None,
                        help="Directory or file where to store the output data")
    parser.add_argument("-f", "--format",
                        dest="format", default=None,
                        help="output data format (can be HDF5)")
    parser.add_argument("-s", "--slow-motor",
                        dest="slow", default=None,
                        help="Dimension of the scan on the slow direction (makes sense only with HDF5)")
    parser.add_argument("-r", "--fast-motor",
                        dest="rapid", default=None,
                        help="Dimension of the scan on the fast direction (makes sense only with HDF5)")
    parser.add_argument("--no-gui",
                        dest="gui", default=True, action="store_false",
                        help="Process the dataset without showing the user interface.")
    parser.add_argument("-j", "--json",
                        dest="json", default=".azimint.json",
                        help="Configuration file containing the processing to be done")
    parser.add_argument("args", metavar='FILE', type=str, nargs='*',
                        help="Files to be integrated")
    parser.add_argument("--monitor-name", dest="monitor_key", default=None,
                        help="Name of the monitor in the header of each input \
                        files. If defined the contribution of each input file \
                        is divided by the monitor. If the header does not \
                        contain or contains a wrong value, the contribution \
                        of the input file is ignored.\
                        On EDF files, values from 'counter_pos' can be accessed \
                        by using the expected mnemonic. \
                        For example 'counter/bmon'.")
    options = parser.parse_args(args)

    # Analysis arguments and options
    args = pyFAI.utils.expand_args(options.args)
    args = sorted(args)

    if options.verbose:
        logger.info("setLevel: debug")
        logger.setLevel(logging.DEBUG)

    if options.debug:
        logging.root.setLevel(logging.DEBUG)

    if options.gui:
        result = integrate_gui(options, args)
    else:
        result = integrate_shell(options, args)
    return result


def main():
    args = sys.argv[1:]
    result = _main(args)
    sys.exit(result)


if __name__ == "__main__":
    main()
