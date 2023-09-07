/*
 *   Project: Azimuthal  integration for PyFAI.
 *            Reduction Kernels
 *
 *
 *   Copyright (C) 2014-2018 European Synchrotron Radiation Facility
 *                           Grenoble, France
 *
 *   Principal authors: Giannis Ashiotis <giannis.ashiotis@gmail.com>
 *   					J. Kieffer (kieffer@esrf.fr)
 *   Last revision: 20/01/2017
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE. */


#include "for_eclipse.h"

__kernel
void reduce1(__global float* buffer,
             __const int length,
             __global float2* preresult) {


    int global_index = get_global_id(0);
    int global_size  = get_global_size(0);
    float2 accumulator;
    accumulator.x = INFINITY;
    accumulator.y = -INFINITY;
    // Loop sequentially over chunks of input vector
    while (global_index < length) {
        float element = buffer[global_index];
        accumulator.x = (accumulator.x < element) ? accumulator.x : element;
        accumulator.y = (accumulator.y > element) ? accumulator.y : element;
        global_index += global_size;
    }

    __local float2 scratch[WORKGROUP_SIZE];

    // Perform parallel reduction
    int local_index = get_local_id(0);

    scratch[local_index] = accumulator;
    barrier(CLK_LOCAL_MEM_FENCE);

    int active_threads = get_local_size(0);

    while (active_threads != 2)
    {
        active_threads /= 2;
        if (thread_id_loc < active_threads)
        {
            float2 other = scratch[local_index + active_threads];
            float2 mine  = scratch[local_index];
            mine.x = (mine.x < other.x) ? mine.x : other.x;
            mine.y = (mine.y > other.y) ? mine.y : other.y;
            /*
            float2 tmp;
            tmp.x = (mine.x < other.x) ? mine.x : other.x;
            tmp.y = (mine.y > other.y) ? mine.y : other.y;
            scratch[local_index] = tmp;
            */
            scratch[local_index] = mine;
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }
    if (local_index == 0) {
        preresult[get_group_id(0)] = scratch[0];
    }
}

__kernel
void reduce2(__global float2* preresult,
             __global float4* result) {


    __local float2 scratch[WORKGROUP_SIZE];

    int local_index = get_local_id(0);

    scratch[local_index] = preresult[local_index];
    barrier(CLK_LOCAL_MEM_FENCE);

    int active_threads = get_local_size(0);

    while (active_threads != 2)
    {
        active_threads /= 2;
        if (thread_id_loc < active_threads)
        {
            float2 other = scratch[local_index + active_threads];
            float2 mine  = scratch[local_index];
            mine.x = (mine.x < other.x) ? mine.x : other.x;
            mine.y = (mine.y > other.y) ? mine.y : other.y;
            /*
            float2 tmp;
            tmp.x = (mine.x < other.x) ? mine.x : other.x;
            tmp.y = (mine.y > other.y) ? mine.y : other.y;
            scratch[local_index] = tmp;
            */
            scratch[local_index] = mine;
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }


    if (local_index == 0) {
        result[0] = vload4(0,scratch);
    }
}
