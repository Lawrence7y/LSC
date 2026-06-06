/**
 * MltMultitrack.h - Multitrack wrapper
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

#ifndef MLTPP_MULTITRACK_H
#define MLTPP_MULTITRACK_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltProducer.h"

namespace Mlt {
class Service;
class Producer;

/** \brief C++ wrapper for ::mlt_multitrack — parallel track container.
 *
 * A multitrack holds multiple producers on parallel tracks and delivers
 * one frame per track per pull. It is the track container inside a Tractor.
 *
 * \extends Producer
 * \see mlt_multitrack_s
 */
class MLTPP_DECLSPEC Multitrack : public Producer
{
private:
    mlt_multitrack instance;

public:
    /** Wrap an existing ::mlt_multitrack handle. */
    Multitrack(mlt_multitrack multitrack);
    Multitrack(Service &multitrack);
    Multitrack(Multitrack &multitrack);
    virtual ~Multitrack();
    mlt_multitrack get_multitrack();
    mlt_producer get_producer() override;
    /** Connect \p producer as track \p index. */
    int connect(Producer &producer, int index);
    /** Insert \p producer at \p index, shifting higher tracks up. */
    int insert(Producer &producer, int index);
    /** Disconnect the track at \p index. */
    int disconnect(int index);
    int clip(mlt_whence whence, int index);
    /** Return the number of tracks. */
    int count();
    /** Return the producer on track \p index. Caller does not own the result. */
    Producer *track(int index);
    /** Recalculate the multitrack length from its tracks. */
    void refresh();
};
} // namespace Mlt

#endif
