/**
 * MltTractor.h - Tractor wrapper
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

#ifndef MLTPP_TRACTOR_H
#define MLTPP_TRACTOR_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltProducer.h"

namespace Mlt {
class Producer;
class Field;
class Multitrack;
class Transition;
class Filter;
class Profile;

/** \brief C++ wrapper for ::mlt_tractor — composites multiple tracks.
 *
 * A tractor owns a Multitrack (parallel track container) and a Field
 * (ordered list of transitions and filters). Together they form the
 * compositing graph for multi-track editing.
 *
 * \extends Producer
 * \see mlt_tractor_s
 */
class MLTPP_DECLSPEC Tractor : public Producer
{
private:
    mlt_tractor instance;

public:
    Tractor();
    Tractor(Profile &profile);
    Tractor(Service &tractor);
    /** Wrap an existing ::mlt_tractor handle. */
    Tractor(mlt_tractor tractor);
    Tractor(Tractor &tractor);
    Tractor(Profile &profile, char *id, char *arg = NULL);
    Tractor(mlt_profile profile, char *id, char *arg = NULL);
    virtual ~Tractor();
    virtual mlt_tractor get_tractor();
    mlt_producer get_producer() override;
    /** Return the underlying Multitrack. Caller does not own the result. */
    Multitrack *multitrack();
    /** Return the Field managing transitions and filters. Caller does not own the result. */
    Field *field();
    /** Recalculate the tractor's length from its tracks. */
    void refresh();
    /** Assign \p producer to track \p index (replaces any existing track). */
    int set_track(Producer &producer, int index);
    /** Insert \p producer at \p index, shifting higher tracks up. */
    int insert_track(Producer &producer, int index);
    /** Remove the track at \p index. */
    int remove_track(int index);
    /** Return the producer on track \p index. Caller does not own the result. */
    Producer *track(int index);
    /** Return the number of tracks. */
    int count();
    /** Plant \p transition between tracks \p a_track and \p b_track. */
    void plant_transition(Transition &transition, int a_track = 0, int b_track = 1);
    void plant_transition(Transition *transition, int a_track = 0, int b_track = 1);
    /** Attach \p filter to track \p track. */
    void plant_filter(Filter &filter, int track = 0);
    void plant_filter(Filter *filter, int track = 0);
    /** Find which track and cut position \p producer occupies. */
    bool locate_cut(Producer *producer, int &track, int &cut);
    int connect(Producer &producer);
};
} // namespace Mlt

#endif
