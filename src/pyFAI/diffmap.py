# coding: utf-8
#
#    Project: Azimuthal integration
#             https://github.com/silx-kit/pyFAI
#
#    Copyright (C) 2015-2024 European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Jérôme Kieffer (Jerome.Kieffer@ESRF.eu)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""Module with GUI for diffraction mapping experiments"""

__author__ = "Jérôme Kieffer"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "MIT"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "01/04/2024"
__status__ = "development"
__docformat__ = 'restructuredtext'

import os
import time
import collections
import glob
from argparse import ArgumentParser
from urllib.parse import urlparse
import logging
logger = logging.getLogger(__name__)
import numpy
import fabio
import json
import __main__ as main
from .opencl import ocl
from . import version as PyFAI_VERSION, date as PyFAI_DATE, load, load_integrators
from .io import Nexus, get_isotime, h5py
from .worker import Worker, _reduce_images
from .method_registry import Method, IntegrationMethod
from .utils.decorators import deprecated, deprecated_warning

DIGITS = [str(i) for i in range(10)]
Position = collections.namedtuple('Position', 'index slow fast')


class DiffMap(object):
    """
    Basic class for diffraction mapping experiment using pyFAI
    """

    def __init__(self, nbpt_fast=0, nbpt_slow=1, nbpt_rad=1000, nbpt_azim=None,
                 **kwargs):
        """Constructor of the class DiffMap for diffraction mapping

        :param npt_fast: number of translations
        :param npt_slow: number of translations
        :param npt_rad: number of points in diffraction pattern (radial dimension)
        :param npt_azim:  number of points in diffraction pattern (azimuthal dimension)
        :param kwargs: former variables named npt_fast, npt_slow, npt_rad, npt_azim which are now deprecated
        """
        self.nbpt_fast = nbpt_fast
        self.nbpt_slow = nbpt_slow
        self.nbpt_rad = nbpt_rad
        self.nbpt_azim = nbpt_azim

        # handle deprecated attributes
        deprecated_args = {"npt_fast", "npt_fast", "npt_rad", "npt_azim"}
        for key in deprecated_args:
            if (key in kwargs):
                valid = key.replace("npt_", "nbpt_")
                self.__setattr__(valid, kwargs.pop(key))
                deprecated_warning("Argument", key, replacement=valid)
        if kwargs:
            raise TypeError(f"DiffMap got unexpected kwargs: {', '.join(kwargs)}")

        self.slow_motor_name = "slow"
        self.fast_motor_name = "fast"
        self.offset = 0
        self.poni = None
        self.worker = Worker(unit="2th_deg", shapeOut=(1, nbpt_rad))
        self.worker.output = "raw"  # exchange IntegrateResults, not numpy arrays
        self.dark = None
        self.flat = None
        self.mask = None  # file containing the mask to be used
        self.I0 = None
        self.hdf5 = None
        self.nxdata_grp = None
        self.dataset = None
        self.dataset_error = None
        self.inputfiles = []
        self.timing = []
        self.stats = False
        self._idx = -1
        self.processed_file = []
        self.stored_input = set()
        self.nxs = None
        self.entry_grp = None
        self.experiment_title = "Diffraction Mapping"
        # method is a property from worker

    def __repr__(self):
        return "%s experiment with nbpt_slow: %s nbpt_fast: %s, nbpt_diff: %s" % \
            (self.experiment_title, self.nbpt_slow, self.nbpt_fast, self.nbpt_rad)

    @staticmethod
    def to_tuple(name):
        """
        Extract numbers as tuple:

        to_tuple("slice06/IRIS4_1_14749.edf")
        --> (6, 4, 1, 14749)

        :param name: input string, often a filename
        """
        res = []
        cur = ""
        for c in name:
            if c in DIGITS:
                cur = cur + c
            elif cur:
                res.append(cur)
                cur = ""
        return tuple(int(i) for i in res)

    def parse(self, sysargv=None, with_config=False):
        """
        Parse options from command line in order to setup the object.
        Does not configure the worker, please use

        :param sysargv: list of arguments passed on the command line (mostly for debug/test)
        :param with_config: parse also the config (as another dict) and return (options, config)
        :return: options, a dictionary able to setup a DiffMapWidget
        """
        description = """Azimuthal integration for diffraction imaging.

Diffraction mapping is an experiment where 2D diffraction patterns are recorded
while performing a 2D scan.

Diff_map is a graphical application (based on pyFAI and h5py) which allows the reduction of this
4D dataset into a 3D dataset containing the two motion dimensions
and the many diffraction angles (thousands). The resulting dataset can be opened using PyMca roitool
where the 1d dataset has to be selected as last dimension.
This result file aims at being NeXus compliant.

This tool can be used for diffraction tomography experiment as well, considering the slow scan direction as the rotation.
        """
        epilog = """Bugs: Many, see hereafter:
1)If the number of files is too large, use double quotes "*.edf"
2)There is a known bug on Debian7 where importing a large number of file can
take much longer than the integration itself: consider passing files in the
command line
        """
        usage = """diff_map [options] -p ponifile imagefiles*
If the number of files is too large, use double quotes like "*.edf" """
        version = "diff_tomo from pyFAI  version %s: %s" % (PyFAI_VERSION, PyFAI_DATE)
        parser = ArgumentParser(usage=usage, description=description, epilog=epilog)
        parser.add_argument("-V", "--version", action='version', version=version)
        parser.add_argument("args", metavar="FILE", help="List of files to integrate. Mandatory without GUI", nargs='*')
        parser.add_argument("-o", "--output", dest="outfile",
                            help="HDF5 File where processed map will be saved. Mandatory without GUI",
                            metavar="FILE", default=None)
        parser.add_argument("-v", "--verbose",
                            action="store_true", dest="verbose", default=False,
                            help="switch to verbose/debug mode, default: quiet")
        parser.add_argument("-P", "--prefix", dest="prefix",
                            help="Prefix or common base for all files",
                            metavar="FILE", default="", type=str)
        parser.add_argument("-e", "--extension", dest="extension",
                            help="Process all files with this extension",
                            default="")
        parser.add_argument("-t", "--fast", dest="fast",
                            help="number of points for the fast motion. Mandatory without GUI", default=None)
        parser.add_argument("-r", "--slow", dest="slow",
                            help="number of points for slow motion. Mandatory without GUI", default=None)
        parser.add_argument("-c", "--npt", dest="npt_rad",
                            help="number of points in diffraction powder pattern. Mandatory without GUI",
                            default=None)
        parser.add_argument("--npt-azim", dest="npt_azim",
                            help="number of points in azimuthal direction, 1 for 1D integration",
                            default=None)
        parser.add_argument("-d", "--dark", dest="dark", metavar="FILE",
                            help="list of dark images to average and subtract (comma separated list)",
                            default=None)
        parser.add_argument("-f", "--flat", dest="flat", metavar="FILE",
                            help="list of flat images to average and divide (comma separated list)",
                            default=None)
        parser.add_argument("-m", "--mask", dest="mask", metavar="FILE",
                            help="file containing the mask, no mask by default", default=None)
        parser.add_argument("-p", "--poni", dest="poni", metavar="FILE",
                            help="file containing the diffraction parameter (poni-file), Mandatory without GUI",
                            default=None)
        parser.add_argument("-O", "--offset", dest="offset",
                            help="do not process the first files", default=None)
        parser.add_argument("-g", "--gpu", dest="gpu", action="store_true",
                            help="process using OpenCL on GPU ", default=False)
        parser.add_argument("-S", "--stats", dest="stats", action="store_true",
                            help="show statistics at the end", default=False)
        parser.add_argument("--gui", dest="gui", action="store_true",
                            help="Use the Graphical User Interface", default=True)
        parser.add_argument("--no-gui", dest="gui", action="store_false",
                            help="Do not use the Graphical User Interface", default=True)
        parser.add_argument("--config", dest="config", default=None,
                            help="provide a JSON configuration file")
        options = parser.parse_args(args=sysargv)
        args = options.args
        if (options.config is not None) and os.path.exists(options.config):
            with open(options.config, "r") as fd:
                config = json.loads(fd.read())
        else:
            config = {}
        self.inputfiles = [i[0] for i in config.get("input_data", [])]
        if "ai" in config:
            ai = config["ai"]
        else:
            ai = {}

        self.poni = config["ai"] = ai
        if "output_file" in config:
            self.hdf5 = config["output_file"]

        if options.verbose:
            logger.setLevel(logging.DEBUG)
        if options.outfile:
            self.hdf5 = options.outfile
            config["output_file"] = self.hdf5
        if options.dark:
            dark_files = [os.path.abspath(urlparse(f).path)
                          for f in options.dark.split(",")
                          if os.path.isfile(urlparse(f).path)]
            if dark_files:
                self.dark = dark_files
                ai["dark_current"] = ",".join(dark_files)
                ai["do_dark"] = True
            else:
                raise RuntimeError("No such dark files")

        if options.flat:
            flat_files = [os.path.abspath(urlparse(f).path)
                          for f in options.flat.split(",")
                          if os.path.isfile(urlparse(f).path)]
            if flat_files:
                self.flat = flat_files
                ai["flat_field"] = ",".join(flat_files)
                ai["do_flat"] = True
            else:
                raise RuntimeError("No such flat files")

        if ocl and options.gpu:
            ai["opencl_device"] = ocl.select_device(type="gpu")
            ai["method"] = ["full", "csr", "opencl"]

        for fn in args:
            f = urlparse(fn).path
            if os.path.isfile(f) and f.endswith(options.extension):
                self.inputfiles.append(os.path.abspath(f))
            elif os.path.isdir(f):
                self.inputfiles += [os.path.abspath(os.path.join(f, g)) for g in os.listdir(f) if g.endswith(options.extension) and g.startswith(options.prefix)]
            else:
                self.inputfiles += [os.path.abspath(f) for f in glob.glob(f)]
        self.inputfiles.sort(key=self.to_tuple)
        config["input_data"] = [(i, None) for i in self.inputfiles]

        if options.mask:
            mask = urlparse(options.mask).path
            if os.path.isfile(mask):
                logger.info("Reading Mask file from: %s", mask)
                self.mask = os.path.abspath(mask)
                ai["mask_file"] = self.mask
                ai["do_mask"] = True
            else:
                logger.warning("No such mask file %s", mask)
        if options.poni:
            if os.path.isfile(options.poni):
                logger.info("Reading PONI file from: %s", options.poni)
                self.poni = options.poni
                ai["poni"] = self.poni
            else:
                logger.warning("No such poni file %s", options.poni)

        deprecated_keys = {
            "fast_motor_points" : "nbpt_fast",
            "slow_motor_points" : "nbpt_slow",
            }
        for key in deprecated_keys:
            if key in config.keys():
                deprecated_warning("Argument", key, deprecated_since="2024.3.0")
                config[deprecated_keys[key]] = config.pop(key)

        if options.fast is None:
            self.nbpt_fast = config.get("nbpt_fast", self.nbpt_fast)
        else:
            self.nbpt_fast = int(options.fast)
            config["nbpt_fast"] = self.nbpt_fast
        if options.slow is None:
            self.nbpt_slow = config.get("nbpt_slow", self.nbpt_slow)
        else:
            self.nbpt_slow = int(options.slow)
            config["nbpt_slow"] = self.nbpt_slow
        if options.npt_rad is not None:
            ai["nbpt_rad"] = self.nbpt_rad = int(options.npt_rad)
        elif "nbpt_rad" in ai:
            self.nbpt_rad = ai["nbpt_rad"]
        if options.npt_azim is not None:
            ai["nbpt_azim"] = self.nbpt_azim = int(options.npt_azim)
        elif "nbpt_azim" in ai:
            self.nbpt_azim = ai["nbpt_azim"]

        if options.offset is not None:
            self.offset = int(options.offset)
            config["offset"] = self.offset
        else:
            self.offset = config.get("offset", 0)
        self.offset = 0 if self.offset is None else self.offset
        self.stats = options.stats

        if with_config:
            if "do_2D" not in ai:
                ai["do_2D"] = False
            if "do_solid_angle" not in ai:
                ai["do_solid_angle"] = True
            if "unit" not in ai:
                ai["unit"] = "2th_deg"
            if "experiment_title" not in config:
                config["experiment_title"] = self.experiment_title
            if "fast_motor_name" not in config:
                config["fast_motor_name"] = self.fast_motor_name
            if "slow_motor_name" not in config:
                config["slow_motor_name"] = self.slow_motor_name
            return options, config
        return options

    def configure_worker(self, dico=None):
        """Configure the worker from the dictionary

        :param dico: dictionary with the configuration
        :return: worker
        """
        self.worker.set_config(dico or self.poni)

    def makeHDF5(self, rewrite=False):
        """
        Create the HDF5 structure if needed ...
        """
        if h5py is None:
            raise RuntimeError("h5py is needed to create HDF5 files")
        dtype = h5py.special_dtype(vlen=str)

        if self.hdf5 is None:
            raise RuntimeError("No output HDF5 file provided")

        logger.info("Initialization of HDF5 file")
        if os.path.exists(self.hdf5) and rewrite:
            os.unlink(self.hdf5)

        nxs = Nexus(self.hdf5, mode="w", creator="pyFAI")
        self.entry_grp = entry_grp = nxs.new_entry(entry="entry",
                                                   program_name="pyFAI",
                                                   title="diff_map")

        process_grp = nxs.new_class(entry_grp, "pyFAI", class_type="NXprocess")
        try:
            process_grp["program"] = main.__file__
        except AttributeError:
            process_grp["program"] = "interactive"
        process_grp["version"] = PyFAI_VERSION
        process_grp["date"] = get_isotime()
        if self.mask:
            process_grp["maskfile"] = self.mask
        if self.flat:
            process_grp["flatfiles"] = numpy.array([i for i in self.flat], dtype=dtype)
        if self.dark:
            process_grp["darkfiles"] = numpy.array([i for i in self.dark], dtype=dtype)
        if isinstance(self.poni, str) and os.path.exists(self.poni):
            process_grp["PONIfile"] = self.poni
        process_grp["inputfiles"] = numpy.array([i for i in self.inputfiles], dtype=dtype)

        process_grp["dim0"] = self.nbpt_slow
        process_grp["dim0"].attrs["axis"] = self.slow_motor_name
        process_grp["dim1"] = self.nbpt_fast
        process_grp["dim1"].attrs["axis"] = self.fast_motor_name
        process_grp["dim2"] = self.nbpt_rad
        process_grp["dim2"].attrs["axis"] = "diffraction"
        config = nxs.new_class(process_grp, "configuration", "NXnote")
        config["type"] = "text/json"
        self.init_shape()
        worker_config = self.worker.get_config()
        # print("Worker configuration:")
        # for k,v in worker_config.items():
        #     print(f"{k}:\t{v}")
        # print("Worker:", self.worker)
        config["data"] = json.dumps(worker_config, indent=2, separators=(",\r\n", ": "))

        self.nxdata_grp = nxs.new_class(process_grp, "result", class_type="NXdata")
        entry_grp.attrs["default"] = self.nxdata_grp.name.split("/", 2)[2]

        if self.worker.do_2D():
            self.dataset = self.nxdata_grp.create_dataset(
                            name="intensity",
                            shape=(self.nbpt_slow, self.nbpt_fast, self.nbpt_azim, self.nbpt_rad),
                            dtype="float32",
                            chunks=(1, 1, self.nbpt_azim, self.nbpt_rad),
                            maxshape=(None, None, self.nbpt_azim, self.nbpt_rad),
                            fillvalue=numpy.nan)
            self.dataset.attrs["interpretation"] = "image"
            self.nxdata_grp.attrs["axes"] = [".", ".", "azimuthal", str(self.unit).split("_")[0]]
            # Build a transposed view to display the mapping experiment
            layout = h5py.VirtualLayout(shape=(self.nbpt_azim, self.nbpt_rad, self.nbpt_slow, self.nbpt_fast), dtype=self.dataset.dtype)
            source = h5py.VirtualSource(self.dataset)
            for i in range(self.nbpt_slow):
                for j in range(self.nbpt_fast):
                    layout[:,:, i, j] = source[i, j]
            self.nxdata_grp.create_virtual_dataset('map', layout, fillvalue=numpy.nan).attrs["interpretation"] = "image"

        else:
            print(f"shape for dataset: {self.nbpt_slow}, {self.nbpt_fast}, {self.nbpt_rad}")
            self.dataset = self.nxdata_grp.create_dataset(
                            name="intensity",
                            shape=(self.nbpt_slow, self.nbpt_fast, self.nbpt_rad),
                            dtype="float32",
                            chunks=(1, self.nbpt_fast, self.nbpt_rad),
                            maxshape=(None, None, self.nbpt_rad),
                            fillvalue=numpy.nan)
            self.dataset.attrs["interpretation"] = "spectrum"
            self.nxdata_grp.attrs["axes"] = [".", ".", str(self.unit).split("_")[0]]
            # Build a transposed view to display the mapping experiment
            layout = h5py.VirtualLayout(shape=(self.nbpt_rad, self.nbpt_slow, self.nbpt_fast), dtype=self.dataset.dtype)
            source = h5py.VirtualSource(self.dataset)
            for i in range(self.nbpt_slow):
                for j in range(self.nbpt_fast):
                    layout[:, i, j] = source[i, j]
            self.nxdata_grp.create_virtual_dataset('map', layout, fillvalue=numpy.nan).attrs["interpretation"] = "image"

        self.nxdata_grp.attrs["signal"] = self.dataset.name.split("/")[-1]

        self.dataset.attrs["title"] = str(self)
        self.nxs = nxs

    def init_shape(self):
        """Initialize the worker with the proper input shape

        :return: shape of the individual frames
        """
        # if shape of detector undefined: reading the first image to guess it
        if self.ai.detector.shape:
            shape = self.ai.detector.shape
        else:
            fimg = fabio.open(self.inputfiles[0])
            shape = fimg.data.shape
            self.worker.ai.shape = shape
            self.worker._shape = shape
        self.worker.output = "raw"
        return shape

    def init_ai(self):
        """Force initialization of azimuthal intgrator

        :return: radial and azimuthal position arrays
        """
        if self.ai is None:
            self.configure_worker(self.poni)
        if not self.nxdata_grp:
            self.makeHDF5(rewrite=False)
        shape = self.init_shape()
        data = numpy.empty(shape, dtype=numpy.float32)
        logger.info(f"Initialization of the Azimuthal Integrator using method {self.method}")
        # enforce initialization of azimuthal integrator
        print(self.ai)
        res = self.worker.process(data)
        tth = res.radial
        if self.dataset is None:
            self.makeHDF5()

        if res.sigma is not None:
            self.dataset_error = self.nxdata_grp.create_dataset("errors",
                                                                shape=self.dataset.shape,
                                                                dtype="float32",
                                                                chunks=(1,) + self.dataset.shape[1:],
                                                                maxshape=(None,) + self.dataset.shape[1:])
            self.dataset_error.attrs["interpretation"] = "image" if self.dataset.ndim == 4 else "spectrum"
        space, unit = str(self.unit).split("_")
        if space not in self.nxdata_grp:
            self.nxdata_grp[space] = tth
            self.nxdata_grp[space].attrs["axes"] = 3
            self.nxdata_grp[space].attrs["unit"] = unit
            self.nxdata_grp[space].attrs["long_name"] = self.unit.label
            self.nxdata_grp[space].attrs["interpretation"] = "scalar"
        if self.worker.do_2D():
            self.nxdata_grp["azimuthal"] = res.azimuthal
            self.nxdata_grp["azimuthal"].attrs["unit"] = "deg"
            self.nxdata_grp["azimuthal"].attrs["interpretation"] = "scalar"
            azim = res.azimuthal
        else:
            azim = None
        return tth, azim

    def show_stats(self):
        if not self.stats:
            return
        try:
            from .gui.matplotlib import pyplot
        except ImportError:
            logger.error("Unable to start matplotlib for display")
            return
        fig = pyplot.figure()
        ax = fig.add_subplot(1, 1, 1)
        ax.hist(self.timing, 500, facecolor='green', alpha=0.75)
        ax.set_xlabel('Execution time (seconds)')
        ax.set_ylabel('Occurence')
        ax.set_title("Execution time")
        ax.grid(True)
        fig.show()
        input("Enter to quit")

    def get_pos(self, filename=None, idx=None):
        """
        Calculate the position in the sinogram of the file according
        to it's number

        :param filename: name of current frame
        :param idx: index of current frame
        :return: namedtuple: index, rot, trans
        """
        if idx is None:
            n = self.inputfiles.index(filename) - self.offset
        else:
            n = idx - self.offset
        return Position(n, n // self.nbpt_fast, n % self.nbpt_fast)

    def process_one_file(self, filename, callback=None):
        """
        :param filename: name of the input filename
        :param idx: index of file
        :param callback: function to be called after every frame has been processed.
        :return: None
        """
        if self.ai is None:
            self.setup_ai()
        if self.dataset is None:
            self.makeHDF5()

        t = time.perf_counter()
        fimg = fabio.open(filename)
        if "dataset" in dir(fimg):
            if isinstance(fimg.dataset, list):
                for ds in fimg.dataset:
                    self.set_hdf5_input_dataset(ds)
            else:
                self.set_hdf5_input_dataset(fimg.dataset)
        self.process_one_frame(fimg.data)
        if callable(callback):
            callback(filename, 0)
        if fimg.nframes > 1:
            for i in range(1, fimg.nframes):
                fimg = fimg.next()
                self.process_one_frame(fimg.data)
                if callable(callback):
                    callback(filename, i + 1)
        t -= time.perf_counter()
        print(f"Processing {os.path.basename(filename):30s} took {-1000*t:6.1f}ms ({fimg.nframes} frames)")
        self.timing.append(-t)
        self.processed_file.append(filename)

    def set_hdf5_input_dataset(self, dataset):
        "record the input dataset with an external link"
        if not isinstance(dataset, h5py.Dataset):
            return
        if not (self.nxs and self.nxs.h5 and self.entry_grp):
            return
        id_ = id(dataset)
        if id_ in self.stored_input:
            return
        else:
            self.stored_input.add(id_)
        # Process 0: measurement group
        if "measurement" in self.entry_grp:
            measurement_grp = self.entry_grp["measurement"]
        else:
            measurement_grp = self.nxs.new_class(self.entry_grp, "measurement", "NXdata")
        here = os.path.dirname(os.path.abspath(self.nxs.filename))
        there = os.path.abspath(dataset.file.filename)
        name = "images_%04i" % len(self.stored_input)
        measurement_grp[name] = h5py.ExternalLink(os.path.relpath(there, here), dataset.name)
        if "signal" not in measurement_grp.attrs:
            measurement_grp.attrs["signal"] = name

    def process_one_frame(self, frame):
        """
        :param frame: 2d numpy array with an image to process
        """
        self._idx += 1
        pos = self.get_pos(None, self._idx)
        shape = self.dataset.shape
        if pos.slow + 1 > shape[0]:
            self.dataset.resize((pos.slow + 1,) + shape[1:])
            if self.dataset_error is not None:
                self.dataset_error.resize((pos.slow + 1,) + shape[1:])
        elif pos.index < 0 or pos.slow < 0 or pos.fast < 0:
            return

        res = self.worker.process(frame)
        self.dataset[pos.slow, pos.fast, ...] = res.intensity
        if res.sigma is not None:
            self.dataset_error[pos.slow, pos.fast, ...] = res.sigma

    def process(self):
        if self.dataset is None:
            self.makeHDF5()
        self.init_ai()
        t0 = time.perf_counter()
        print(self.inputfiles)
        for f in self.inputfiles:
            self.process_one_file(f)
        tot = time.perf_counter() - t0
        cnt = max(self._idx, 0) + 1
        print(f"Execution time for {cnt} frames: {tot:.3f} s; "
              f"Average execution time: {1000. * tot / cnt:.1f} ms/img")
        self.nxs.close()

    def get_use_gpu(self):
        return self.worker._method.impl_lower == "opencl"

    def set_use_gpu(self, value):
        if self.worker:
            if value:
                method = self.worker._method.method.fixed("opencl")
            else:
                method = self.worker._method.method.fixed("cython")
            self.worker.set_method(method)

    use_gpu = property(get_use_gpu, set_use_gpu)

    @property
    def ai(self):
        "return the azimuthal integrator stored in the worker, replaces the attribute"
        if self.worker is None:
            return None
        else:
            return self.worker.ai

    @ai.setter
    def ai(self, value):
        if self.worker is None:
            self.worker = Worker(value, unit=self.unit, shapeOut=(1, self.nbpt_rad))
        else:
            self.worker.ai = value

    @property
    def method(self):
        if self.worker is not None:
            return self.worker.method
        return None

    @method.setter
    def method(self, value):
        self.worker.set_method(value)

    @property
    def unit(self):
        return self.worker.unit

    @unit.setter
    def unit(self, value):
        self.worker.unit = value

    @property
    @deprecated(replacement="nbpt_fast")
    def npt_fast(self):
        return self.nbpt_fast

    @npt_fast.setter
    @deprecated(replacement="nbpt_fast")
    def npt_fast(self, value):
        self.nbpt_fast = value

    @property
    @deprecated(replacement="nbpt_slow")
    def npt_slow(self):
        return self.nbpt_slow

    @npt_slow.setter
    @deprecated(replacement="nbpt_slow")
    def npt_slow(self, value):
        self.nbpt_slow = value

    @property
    @deprecated(replacement="nbpt_rad")
    def npt_rad(self):
        return self.nbpt_rad

    @npt_rad.setter
    @deprecated(replacement="nbpt_rad")
    def npt_rad(self, value):
        self.nbpt_rad = value

    @property
    @deprecated(replacement="nbpt_azim")
    def npt_azim(self):
        return self.nbpt_azim

    @npt_azim.setter
    @deprecated(replacement="nbpt_azim")
    def npt_azim(self, value):
        self.nbpt_azim = value
