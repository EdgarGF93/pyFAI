# coding: utf-8
#cython: embedsignature=True, language_level=3, binding=True
#cython: boundscheck=False, wraparound=False, cdivision=True, initializedcheck=False,
## This is for developping
## cython: profile=True, warn.undeclared=True, warn.unused=True, warn.unused_result=False, warn.unused_arg=True
#
#    Project: Fast Azimuthal integration
#             https://github.com/silx-kit/pyFAI
#
#    Copyright (C) 2012-2021 European Synchrotron Radiation Facility, Grenoble, France
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

"""Calculates histograms of pos0 (tth) weighted by Intensity

Splitting is done on the pixel's bounding box like fit2D,
reverse implementation based on a sparse matrix multiplication
"""

__author__ = "Jerome Kieffer"
__contact__ = "Jerome.kieffer@esrf.fr"
__date__ = "22/11/2021"
__status__ = "stable"
__license__ = "MIT"

include "regrid_common.pxi"
include "LUT_common.pxi"

import cython
import os
import sys
import logging
logger = logging.getLogger(__name__)
from cython.parallel import prange
from libc.string cimport memset, memcpy
from cython cimport view
import numpy
cimport numpy
from ..utils import crc32
from ..utils.decorators import deprecated
from .sparse_builder cimport SparseBuilder

if LUT_ITEMSIZE == lut_d.itemsize == 8:
    logger.debug("LUT sizes C:%s \t Numpy: %s", lut_d.itemsize, LUT_ITEMSIZE)
else:
    logger.error("LUT sizes C:%s \t Numpy: %s", lut_d.itemsize, LUT_ITEMSIZE)
    raise ImportError("Numpy and C have the same internal LUT representation")


def int0(a):
    try:
        res = int(a)
    except ValueError:
        res = 0
    return res


class HistoBBox1d(LutIntegrator):
    """
    1D histogramming with pixel splitting based on a Look-up table

    The initialization of the class can take quite a while (operation are not parallelized)
    but each integrate is parallelized and quite efficient.
    """

    @cython.boundscheck(False)
    def __init__(self,
                 pos0,
                 delta_pos0,
                 pos1=None,
                 delta_pos1=None,
                 int bins=100,
                 pos0_range=None,
                 pos1_range=None,
                 mask=None,
                 mask_checksum=None,
                 allow_pos0_neg=False,
                 unit="undefined",
                 empty=0.0):
        """
        :param pos0: 1D array with pos0: tth or q_vect or r ...
        :param delta_pos0: 1D array with delta pos0: max center-corner distance
        :param pos1: 1D array with pos1: chi
        :param delta_pos1: 1D array with max pos1: max center-corner distance, unused !
        :param bins: number of output bins, 100 by default
        :param pos0_range: minimum and maximum  of the 2th range
        :param pos1_range: minimum and maximum  of the chi range
        :param mask: array (of int8) with masked pixels with 1 (0=not masked)
        :param allow_pos0_neg: enforce the q<0 is usually not possible
        :param unit: can be 2th_deg or r_nm^-1 ...
        :param empty: value for bins without contributing pixels
        """

        self.size = pos0.size
        if "size" not in dir(delta_pos0) or delta_pos0.size != self.size:
            logger.warning("Pixel splitting desactivated !")
            delta_pos0 = None
        self.bins = bins
        #self.lut_size = 0
        self.allow_pos0_neg = allow_pos0_neg
        #self.empty = empty
        if mask is not None:
            assert mask.size == self.size, "mask size"
            self.check_mask = True
            self.cmask = numpy.ascontiguousarray(mask.ravel(), dtype=mask_d)
            if mask_checksum:
                self.mask_checksum = mask_checksum
            else:
                self.mask_checksum = crc32(mask)
        else:
            self.check_mask = False
            self.mask_checksum = None

        self.pos0_range = pos0_range
        self.pos1_range = pos1_range
        self.cpos0 = numpy.ascontiguousarray(pos0.ravel(), dtype=position_d)
        if delta_pos0 is None:
            self.calc_boundaries_nosplit(pos0_range)
        else:
            self.dpos0 = numpy.ascontiguousarray(delta_pos0.ravel(), dtype=position_d)
            self.cpos0_sup = numpy.empty_like(self.cpos0)  # self.cpos0 + self.dpos0
            self.cpos0_inf = numpy.empty_like(self.cpos0)  # self.cpos0 - self.dpos0
            self.calc_boundaries(pos0_range)
        if pos1_range is not None:
            assert pos1.size == self.size, "pos1 size"
            assert delta_pos1.size == self.size, "delta_pos1.size == self.size"
            self.check_pos1 = True
            self.cpos1_min = numpy.ascontiguousarray((pos1 - delta_pos1).ravel(), dtype=position_d)
            self.cpos1_max = numpy.ascontiguousarray((pos1 + delta_pos1).ravel(), dtype=position_d)
            self.pos1_min, pos1_maxin = pos1_range
            self.pos1_max = calc_upper_bound(<position_t> pos1_maxin)
        else:
            self.check_pos1 = False
            self.cpos1_min = None
            self.pos1_max = None

        self.delta = (self.pos0_max - self.pos0_min) / (<position_t> bins)

        self.lut_max_idx = None
        self._lut_checksum = None
        if delta_pos0 is not None:
            lut = self.calc_lut()
        else:
            lut = self.calc_lut_nosplit()

        #Call the constructor of the parent class
        super().__init__(lut, pos0.size, empty)

        self.bin_centers = numpy.linspace(self.pos0_min + 0.5 * self.delta,
                                          self.pos0_max - 0.5 * self.delta,
                                          self.bins)

        self.unit = unit
        self.lut_nbytes = lut.nbytes
        self.lut_checksum = crc32(numpy.asarray(lut))

    def calc_boundaries(self, pos0_range):
        """
        Called by constructor to calculate the boundaries and the bin position
        """
        cdef:
            int idx, size = self.cpos0.size
            bint check_mask = self.check_mask
            mask_t[::1] cmask
            position_t[::1] cpos0, dpos0, cpos0_sup, cpos0_inf,
            position_t upper, lower, pos0_max, pos0_min, c, d
            bint allow_pos0_neg = self.allow_pos0_neg

        cpos0_sup = self.cpos0_sup
        cpos0_inf = self.cpos0_inf
        cpos0 = self.cpos0
        dpos0 = self.dpos0
        pos0_min = pos0_max = cpos0[0]
        if not allow_pos0_neg and pos0_min < 0:
                    pos0_min = pos0_max = 0
        if check_mask:
            cmask = self.cmask
        with nogil:
            for idx in range(size):
                c = cpos0[idx]
                d = dpos0[idx]
                lower = c - d
                upper = c + d
                cpos0_sup[idx] = upper
                cpos0_inf[idx] = lower
                if not allow_pos0_neg and lower < 0:
                    lower = 0
                if not (check_mask and cmask[idx]):
                    if upper > pos0_max:
                        pos0_max = upper
                    if lower < pos0_min:
                        pos0_min = lower

        if pos0_range is not None:
            self.pos0_min, self.pos0_maxin = pos0_range
        else:
            self.pos0_min = pos0_min
            self.pos0_maxin = pos0_max
        if (not allow_pos0_neg) and self.pos0_min < 0:
            self.pos0_min = 0
        self.pos0_max = calc_upper_bound(<position_t> self.pos0_maxin)

    def calc_boundaries_nosplit(self, pos0_range):
        """
        Calculate self.pos0_min and self.pos0_max when no splitting is requested

        :param pos0_range: 2-tuple containing the requested range
        """
        cdef:
            int size = self.cpos0.size
            bint check_mask = self.check_mask
            mask_t[::1] cmask
            position_t[::1] cpos0
            position_t pos0_max, pos0_min, c
            bint allow_pos0_neg = self.allow_pos0_neg
            int idx

        if pos0_range is not None:
            self.pos0_min, self.pos0_maxin = pos0_range
        else:
            cpos0 = self.cpos0
            pos0_min = pos0_max = cpos0[0]

            if not allow_pos0_neg and pos0_min < 0:
                pos0_min = pos0_max = 0

            if check_mask:
                cmask = self.cmask

            with nogil:
                for idx in range(size):
                    c = cpos0[idx]
                    if not allow_pos0_neg and c < 0:
                        c = 0
                    if not (check_mask and cmask[idx]):
                        if c > pos0_max:
                            pos0_max = c
                        if c < pos0_min:
                            pos0_min = c
            self.pos0_min = pos0_min
            self.pos0_maxin = pos0_max

        if (not allow_pos0_neg) and self.pos0_min < 0:
            self.pos0_min = 0
        self.pos0_max = calc_upper_bound(<position_t> self.pos0_maxin)

    def calc_lut(self):
        """Use BoundingBox splitting to calculate the content of the LUT

        :return: Look-up table (2D array of lut_d) 
        """
        cdef:
            position_t delta = self.delta, pos0_min = self.pos0_min, pos1_min, pos1_max, min0, max0, fbin0_min, fbin0_max
            acc_t delta_left, delta_right, inv_area
            int k, idx, bin0_min, bin0_max, bins = self.bins, lut_size, i, size = self.size
            bint check_mask, check_pos1
            position_t[::1] cpos0_sup = self.cpos0_sup
            position_t[::1] cpos0_inf = self.cpos0_inf
            position_t[::1] cpos1_min, cpos1_max
            mask_t[::1] cmask
            SparseBuilder builder = SparseBuilder(bins, block_size=32, heap_size=size)

        
        if self.check_mask:
            cmask = self.cmask
            check_mask = True
        else:
            check_mask = False

        if self.check_pos1:
            check_pos1 = True
            cpos1_min = self.cpos1_min
            cpos1_max = self.cpos1_max
            pos1_max = self.pos1_max
            pos1_min = self.pos1_min
        else:
            check_pos1 = False
        
        with nogil:
            for idx in range(size):
                if (check_mask) and (cmask[idx]):
                    continue

                min0 = cpos0_inf[idx]
                max0 = cpos0_sup[idx]

                if check_pos1 and ((cpos1_max[idx] < pos1_min) or (cpos1_min[idx] > pos1_max)):
                    continue

                fbin0_min = get_bin_number(min0, pos0_min, delta)
                fbin0_max = get_bin_number(max0, pos0_min, delta)
                bin0_min = <int> fbin0_min
                bin0_max = <int> fbin0_max

                if (bin0_max < 0) or (bin0_min >= bins):
                    continue
                if bin0_max >= bins:
                    bin0_max = bins - 1
                if bin0_min < 0:
                    bin0_min = 0

                if bin0_min == bin0_max:
                    # All pixel is within a single bin
                    builder.cinsert(bin0_min, idx, 1.0)
                else:
                    # we have pixel splitting.
                    inv_area = 1.0 / (fbin0_max - fbin0_min)

                    delta_left = <acc_t>(bin0_min + 1) - fbin0_min
                    delta_right = fbin0_max - (<acc_t> bin0_max)

                    builder.cinsert(bin0_min, idx, inv_area * delta_left)
                    builder.cinsert(bin0_max, idx,  inv_area * delta_right)

                    if bin0_min + 1 < bin0_max:
                        for i in range(bin0_min + 1, bin0_max):
                            builder.cinsert(i, idx,  inv_area)
                            
        return builder.to_lut()

    def calc_lut_nosplit(self):
        """Calculate the content of the LUT without pixel splitting

        :return: Look-up table (2D array of lut_d) 
        """

        cdef:
            position_t delta = self.delta, pos0_min = self.pos0_min, pos1_min, pos1_max, fbin0, pos0
            int32_t k, idx, bin0, bins = self.bins, nnz, size = self.size
            bint check_mask, check_pos1
            position_t[::1] cpos0 = self.cpos0, cpos1_min, cpos1_max,
            mask_t[::1] cmask
            SparseBuilder builder = SparseBuilder(bins, block_size=32, heap_size=size)

        if self.check_mask:
            cmask = self.cmask
            check_mask = True
        else:
            check_mask = False

        if self.check_pos1:
            check_pos1 = True
            cpos1_min = self.cpos1_min
            cpos1_max = self.cpos1_max
            pos1_max = self.pos1_max
            pos1_min = self.pos1_min
        else:
            check_pos1 = False

        
        with nogil:
            for idx in range(size):
                if (check_mask) and (cmask[idx]):
                    continue

                pos0 = cpos0[idx]

                if check_pos1 and ((cpos1_max[idx] < pos1_min) or (cpos1_min[idx] > pos1_max)):
                    continue

                fbin0 = get_bin_number(pos0, pos0_min, delta)
                bin0 = < int > fbin0

                if (bin0 < 0) or (bin0 >= bins):
                    continue
                builder.cinsert(bin0, idx, onef)
        return builder.to_lut()

    @property
    @deprecated(replacement="bin_centers", since_version="0.16", only_once=True)
    def outPos(self):
        return self.bin_centers


################################################################################
# Bidimensionnal regrouping
################################################################################


class HistoBBox2d(LutIntegrator):
    """
    2D histogramming with pixel splitting based on a look-up table

    The initialization of the class can take quite a while (operation are not parallelized)
    but each integrate is parallelized and quite efficient.
    """
    @cython.boundscheck(False)
    def __init__(self,
                 pos0,
                 delta_pos0,
                 pos1,
                 delta_pos1,
                 bins=(100, 36),
                 pos0_range=None,
                 pos1_range=None,
                 mask=None,
                 mask_checksum=None,
                 allow_pos0_neg=False,
                 unit="undefined",
                 chiDiscAtPi=True,
                 empty=0.0
                 ):
        """
        :param pos0: 1D array with pos0: tth or q_vect
        :param delta_pos0: 1D array with delta pos0: max center-corner distance
        :param pos1: 1D array with pos1: chi
        :param delta_pos1: 1D array with max pos1: max center-corner distance, unused !
        :param bins: number of output bins (tth=100, chi=36 by default)
        :param pos0_range: minimum and maximum  of the 2th range
        :param pos1_range: minimum and maximum  of the chi range
        :param mask: array (of int8) with masked pixels with 1 (0=not masked)
        :param allow_pos0_neg: enforce the q<0 is usually not possible
        :param chiDiscAtPi: boolean; by default the chi_range is in the range ]-pi,pi[ set to 0 to have the range ]0,2pi[
        :param unit: can be 2th_deg or r_nm^-1 ...
        """
        cdef:
            int32_t size, bin0, bin1
        self.size = pos0.size
        assert delta_pos0.size == self.size, "delta_pos0.size == self.size"
        assert pos1.size == self.size, "pos1 size"
        assert delta_pos1.size == self.size, "delta_pos1.size == self.size"
        self.chiDiscAtPi = 1 if chiDiscAtPi else 0
        self.allow_pos0_neg = allow_pos0_neg

        try:
            bins0, bins1 = tuple(bins)
        except TypeError:
            bins0 = bins1 = bins
        if bins0 <= 0:
            bins0 = 1
        if bins1 <= 0:
            bins1 = 1
        self.bins = (int(bins0), int(bins1))
        # self.lut_size = 0
        if mask is not None:
            assert mask.size == self.size, "mask size"
            self.check_mask = True
            self.cmask = numpy.ascontiguousarray(mask.ravel(), dtype=numpy.int8)
            if mask_checksum:
                self.mask_checksum = mask_checksum
            else:
                self.mask_checksum = crc32(mask)
        else:
            self.check_mask = False
            self.mask_checksum = None

        self.cpos0 = numpy.ascontiguousarray(pos0.ravel(), dtype=position_d)
        self.dpos0 = numpy.ascontiguousarray(delta_pos0.ravel(), dtype=position_d)
        self.cpos0_sup = numpy.empty_like(self.cpos0)
        self.cpos0_inf = numpy.empty_like(self.cpos0)
        self.pos0_range = pos0_range
        self.pos1_range = pos1_range

        self.cpos1 = numpy.ascontiguousarray((pos1).ravel(), dtype=position_d)
        self.dpos1 = numpy.ascontiguousarray((delta_pos1).ravel(), dtype=position_d)
        self.cpos1_sup = numpy.empty_like(self.cpos1)
        self.cpos1_inf = numpy.empty_like(self.cpos1)
        self.calc_boundaries(pos0_range, pos1_range)
        self.delta0 = (self.pos0_max - self.pos0_min) / float(bins0)
        self.delta1 = (self.pos1_max - self.pos1_min) / float(bins1)
        self.lut_max_idx = None
        # self._lut = None
        if delta_pos0 is not None:
            lut = self.calc_lut()
        else:
            lut = self.calc_lut_nosplit()

        #Call the constructor of the parent class
        super().__init__(numpy.asarray(lut).reshape((bins0*bins1, -1)), pos0.size, empty)
        self.bin_centers = None
        self.bin_centers0 = numpy.linspace(self.pos0_min + 0.5 * self.delta0,
                                           self.pos0_max - 0.5 * self.delta0,
                                           bins0)
        self.bin_centers1 = numpy.linspace(self.pos1_min + 0.5 * self.delta1,
                                           self.pos1_max - 0.5 * self.delta1,
                                           bins1)
        self.unit = unit
        self.lut_checksum = crc32(numpy.asarray(lut))

    def calc_boundaries(self, pos0_range, pos1_range):
        """
        Called by constructor to calculate the boundaries and the bin position
        """
        cdef:
            int32_t idx, size = self.cpos0.size
            bint check_mask = self.check_mask
            mask_t[::1] cmask
            position_t[::1] cpos0, dpos0, cpos0_sup, cpos0_inf
            position_t[::1] cpos1, dpos1, cpos1_sup, cpos1_inf,
            position_t upper0, lower0, pos0_max, pos0_min, c0, d0
            position_t upper1, lower1, pos1_max, pos1_min, c1, d1
            bint allow_pos0_neg = self.allow_pos0_neg
            bint chiDiscAtPi = self.chiDiscAtPi

        cpos0_sup = self.cpos0_sup
        cpos0_inf = self.cpos0_inf
        cpos0 = self.cpos0
        dpos0 = self.dpos0
        cpos1_sup = self.cpos1_sup
        cpos1_inf = self.cpos1_inf
        cpos1 = self.cpos1
        dpos1 = self.dpos1
        pos0_min = cpos0[0]
        pos0_max = cpos0[0]
        pos1_min = cpos1[0]
        pos1_max = cpos1[0]

        if check_mask:
            cmask = self.cmask
        with nogil:
            for idx in range(size):
                c0 = cpos0[idx]
                d0 = dpos0[idx]
                lower0 = c0 - d0
                upper0 = c0 + d0
                c1 = cpos1[idx]
                d1 = dpos1[idx]
                lower1 = c1 - d1
                upper1 = c1 + d1
                if not allow_pos0_neg and lower0 < 0:
                    lower0 = 0
                if upper1 > (2 - chiDiscAtPi) * pi:
                    upper1 = (2 - chiDiscAtPi) * pi
                if lower1 < (-chiDiscAtPi) * pi:
                    lower1 = (-chiDiscAtPi) * pi
                cpos0_sup[idx] = upper0
                cpos0_inf[idx] = lower0
                cpos1_sup[idx] = upper1
                cpos1_inf[idx] = lower1
                if not (check_mask and cmask[idx]):
                    if upper0 > pos0_max:
                        pos0_max = upper0
                    if lower0 < pos0_min:
                        pos0_min = lower0
                    if upper1 > pos1_max:
                        pos1_max = upper1
                    if lower1 < pos1_min:
                        pos1_min = lower1

        if pos0_range is not None:
            self.pos0_min, self.pos0_maxin = pos0_range
        else:
            self.pos0_min = pos0_min
            self.pos0_maxin = pos0_max

        if pos1_range is not None:
            self.pos1_min, self.pos1_maxin = pos1_range
        else:
            self.pos1_min = pos1_min
            self.pos1_maxin = pos1_max

        if (not allow_pos0_neg) and self.pos0_min < 0:
            self.pos0_min = 0
        self.pos0_max = calc_upper_bound(<position_t>  self.pos0_maxin)
        self.cpos0_sup = cpos0_sup
        self.cpos0_inf = cpos0_inf
        self.pos1_max = calc_upper_bound(<position_t>  self.pos1_maxin)
        self.cpos1_sup = cpos1_sup
        self.cpos1_inf = cpos1_inf

    def calc_lut(self):
        'calculate the max number of elements in the LUT and populate it'
        cdef:
            position_t delta0 = self.delta0, pos0_min = self.pos0_min, min0, max0, fbin0_min, fbin0_max
            position_t delta1 = self.delta1, pos1_min = self.pos1_min, min1, max1, fbin1_min, fbin1_max
            int bin0_min, bin0_max, bins0 = self.bins[0]
            int bin1_min, bin1_max, bins1 = self.bins[1]
            int k, idx, lut_size, i, j, size = self.size
            bint check_mask
            position_t[::1] cpos0_sup = self.cpos0_sup
            position_t[::1] cpos0_inf = self.cpos0_inf
            position_t[::1] cpos1_inf = self.cpos1_inf
            position_t[::1] cpos1_sup = self.cpos1_sup
            int32_t[:, ::1] outmax = numpy.zeros((bins0, bins1), dtype=numpy.int32)
            lut_t[:, :, ::1] lut
            mask_t[:] cmask
            acc_t inv_area, delta_down, delta_up, delta_right, delta_left
            SparseBuilder builder = SparseBuilder(bins0*bins1, block_size=8, heap_size=size)

        if self.check_mask:
            cmask = self.cmask
            check_mask = True
        else:
            check_mask = False

        # NOGIL
        with nogil:
            for idx in range(size):
                if (check_mask) and cmask[idx]:
                    continue

                min0 = cpos0_inf[idx]
                max0 = cpos0_sup[idx]
                min1 = cpos1_inf[idx]
                max1 = cpos1_sup[idx]

                fbin0_min = get_bin_number(min0, pos0_min, delta0)
                fbin0_max = get_bin_number(max0, pos0_min, delta0)
                fbin1_min = get_bin_number(min1, pos1_min, delta1)
                fbin1_max = get_bin_number(max1, pos1_min, delta1)

                bin0_min = <int> fbin0_min
                bin0_max = <int> fbin0_max
                bin1_min = <int> fbin1_min
                bin1_max = <int> fbin1_max

                if (bin0_max < 0) or (bin0_min >= bins0) or (bin1_max < 0) or (bin1_min >= bins1):
                    continue

                if bin0_max >= bins0:
                    bin0_max = bins0 - 1
                if bin0_min < 0:
                    bin0_min = 0
                if bin1_max >= bins1:
                    bin1_max = bins1 - 1
                if bin1_min < 0:
                    bin1_min = 0

                if bin0_min == bin0_max:
                    if bin1_min == bin1_max:
                        # All pixel is within a single bin
                        builder.cinsert(bin0_min*bins1+bin1_min, idx, 1.0)

                    else:
                        # spread on more than 2 bins
                        delta_down = (<acc_t> (bin1_min + 1)) - fbin1_min
                        delta_up = fbin1_max - <acc_t> bin1_max
                        inv_area = 1.0 / (fbin1_max - fbin1_min)

                        builder.cinsert(bin0_min*bins1+bin1_min, idx, inv_area * delta_down)

                        builder.cinsert(bin0_min*bins1+bin1_max, idx, inv_area * delta_up)

                        for j in range(bin1_min + 1, bin1_max):
                            builder.cinsert(bin0_min*bins1+j, idx, inv_area)

                else:
                    # spread on more than 2 bins in dim 0
                    if bin1_min == bin1_max:
                        # All pixel fall on 1 bins in dim 1
                        inv_area = 1.0 / (fbin0_max - fbin0_min)
                        delta_left = (<acc_t> (bin0_min + 1)) - fbin0_min
                        builder.cinsert(bin0_min*bins1+bin1_min, idx, inv_area * delta_left)

                        delta_right = fbin0_max - (<acc_t> bin0_max)
                        builder.cinsert(bin0_max*bins1+bin1_min, idx, inv_area * delta_right)

                        for i in range(bin0_min + 1, bin0_max):
                            builder.cinsert(i*bins1+bin1_min, idx, inv_area)

                    else:
                        # spread on n pix in dim0 and m pixel in dim1:
                        delta_left = (<acc_t> (bin0_min + 1)) - fbin0_min
                        delta_right = fbin0_max - (<acc_t> bin0_max)
                        delta_down = (<acc_t> (bin1_min + 1)) - fbin1_min
                        delta_up = fbin1_max - (<acc_t> bin1_max)
                        inv_area = 1.0 / ((fbin0_max - fbin0_min) * (fbin1_max - fbin1_min))

                        builder.cinsert(bin0_min*bins1+bin1_min, idx, inv_area * delta_left * delta_down)
                        builder.cinsert(bin0_min*bins1+bin1_max, idx, inv_area * delta_left * delta_up)
                        builder.cinsert(bin0_max*bins1+bin1_min, idx, inv_area * delta_right * delta_down)
                        builder.cinsert(bin0_max*bins1+bin1_max, idx, inv_area * delta_right * delta_up)

                        for i in range(bin0_min + 1, bin0_max):
                            builder.cinsert(i*bins1+bin1_min, idx, inv_area * delta_down)

                            for j in range(bin1_min + 1, bin1_max):
                                builder.cinsert(i*bins1+j, idx, inv_area)

                            builder.cinsert(i*bins1+bin1_max, idx, inv_area * delta_up)

                        for j in range(bin1_min + 1, bin1_max):
                            builder.cinsert(bin0_min*bins1+j, idx, inv_area * delta_left)

                            builder.cinsert(bin0_max*bins1+j, idx, inv_area * delta_right)

        return builder.to_lut()

    @property
    @deprecated(replacement="bin_centers0", since_version="0.16", only_once=True)
    def outPos0(self):
        return self.bin_centers0

    @property
    @deprecated(replacement="bin_centers1", since_version="0.16", only_once=True)
    def outPos1(self):
        return self.bin_centers1
