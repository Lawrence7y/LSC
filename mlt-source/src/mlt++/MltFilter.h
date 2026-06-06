/**
 * MltFilter.h - MLT Wrapper
 * Copyright (C) 2004-2026 Meltytech, LLC
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
 */

#ifndef MLTPP_FILTER_H
#define MLTPP_FILTER_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltService.h"

namespace Mlt {
class Service;
class Profile;
class Frame;

/** \brief C++ wrapper for ::mlt_filter — modifies a single producer's output.
 *
 * A filter is attached to a service (typically a producer or another filter)
 * and can inspect or modify each frame as it passes through the service
 * network. Set the \em disable property to 1 to bypass the filter.
 *
 * \extends Service
 * \see mlt_filter_s
 */
class MLTPP_DECLSPEC Filter : public Service
{
private:
    mlt_filter instance;

public:
    Filter();
    /** Construct and instantiate filter \p id from the repository. */
    Filter(Profile &profile, const char *id, const char *service = NULL);
    Filter(mlt_profile profile, const char *id, const char *service = NULL);
    Filter(Service &filter);
    Filter(Filter &filter);
    Filter(const Filter &filter);
    /** Wrap an existing ::mlt_filter handle. */
    Filter(mlt_filter filter);
    Filter(Filter *filter);
    virtual ~Filter();
    Filter &operator=(const Filter &filter);
    virtual mlt_filter get_filter();
    mlt_service get_service() override;
    /** Connect this filter to \p service at \p index. */
    int connect(Service &service, int index = 0);
    /** Restrict filter activity to the frame range [\p in, \p out]. */
    void set_in_and_out(int in, int out);
    /** Return the in-point frame number. */
    int get_in();
    /** Return the out-point frame number. */
    int get_out();
    /** Return the duration (out - in + 1). */
    int get_length();
    /** Return the duration relative to \p frame's producer length. */
    int get_length2(Frame &frame);
    /** Return the multitrack track index on which this filter is applied. */
    int get_track();
    /** Return the current position of \p frame relative to the filter's in-point. */
    int get_position(Frame &frame);
    /** Return the playback progress of \p frame in the range [0.0, 1.0]. */
    double get_progress(Frame &frame);
    /** Run the filter's process function on \p frame (used by subclasses). */
    void process(Frame &frame);
};
} // namespace Mlt

#endif
