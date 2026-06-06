/**
 * MltTransition.h - MLT Wrapper
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

#ifndef MLTPP_TRANSITION_H
#define MLTPP_TRANSITION_H

#include "MltConfig.h"

#include "MltService.h"
#include <framework/mlt.h>

namespace Mlt {
class Service;
class Profile;
class Frame;
class Chain;
class Link;

/** \brief C++ wrapper for ::mlt_transition — blends two tracks together.
 *
 * A transition composites the B-track over the A-track for a specified frame
 * range and track pair. It is planted in a Tractor's Field via
 * Tractor::plant_transition().
 *
 * \extends Service
 * \see mlt_transition_s
 */
class MLTPP_DECLSPEC Transition : public Service
{
private:
    mlt_transition instance;

public:
    Transition();
    /** Construct and instantiate transition \p id from the repository. */
    Transition(Profile &profile, const char *id, const char *arg = NULL);
    Transition(mlt_profile profile, const char *id, const char *arg = NULL);
    Transition(Service &transition);
    Transition(Transition &transition);
    Transition(const Transition &transition);
    /** Wrap an existing ::mlt_transition handle. */
    Transition(mlt_transition transition);
    virtual ~Transition();
    Transition &operator=(const Transition &transition);
    virtual mlt_transition get_transition();
    mlt_service get_service() override;
    /** Restrict this transition to the frame range [\p in, \p out]. */
    void set_in_and_out(int in, int out);
    /** Set which tracks this transition blends (A = lower, B = upper). */
    void set_tracks(int a_track, int b_track);
    int connect(Producer &producer, int a_track, int b_track);
    int connect(Service &service, int a_track, int b_track);
    /** Return the A-track index. */
    int get_a_track();
    /** Return the B-track index. */
    int get_b_track();
    /** Return the in-point frame number. */
    int get_in();
    /** Return the out-point frame number. */
    int get_out();
    /** Return the duration (out - in + 1). */
    int get_length();
    /** Return the current position of \p frame relative to the transition's in-point. */
    int get_position(Frame &frame);
    /** Return the playback progress in the range [0.0, 1.0]. */
    double get_progress(Frame &frame);
    /** Return the per-frame progress delta. */
    double get_progress_delta(Frame &frame);
};
} // namespace Mlt

#endif
