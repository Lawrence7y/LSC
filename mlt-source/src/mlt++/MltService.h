/**
 * MltService.h - MLT Wrapper
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

#ifndef MLTPP_SERVICE_H
#define MLTPP_SERVICE_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltFrame.h"
#include "MltProperties.h"

namespace Mlt {
class Properties;
class Filter;
class Frame;
class Profile;

/** \brief C++ wrapper for ::mlt_service — abstract base for all MLT services.
 *
 * A service can have multiple input connections (producers) and a single
 * output connection (consumer). Services that sit between producers and a
 * consumer modify data and are called filters. Any service can have zero or
 * more filters attached to it.
 *
 * \extends Properties
 * \see mlt_service_s
 */
class MLTPP_DECLSPEC Service : public Properties
{
private:
    mlt_service instance;

public:
    Service();
    Service(Service &service);
    Service(const Service &service);
    /** Wrap an existing ::mlt_service handle. */
    Service(mlt_service service);
    Service(Service *service);
    virtual ~Service();
    Service &operator=(const Service &service);
    virtual mlt_service get_service();
    /** Acquire the service mutex (use around property reads in render threads). */
    void lock();
    /** Release the service mutex. */
    void unlock();
    virtual mlt_properties get_properties() override;
    /** Connect \p producer as the input at \p index. */
    int connect_producer(Service &producer, int index = 0);
    /** Insert \p producer at \p index, shifting existing connections up. */
    int insert_producer(Service &producer, int index = 0);
    /** Disconnect the producer at \p index. */
    int disconnect_producer(int index = 0);
    /** Disconnect all producer inputs. */
    int disconnect_all_producers();
    void set_consumer(Service &service);
    /** Return the downstream consumer service, or null. */
    Service *consumer();
    /** Return the first upstream producer, or null. */
    Service *producer();
    /** Return the profile associated with this service. */
    Profile *profile();
    mlt_profile get_profile();
    /** Pull and return frame \p index from this service. Caller owns the result. */
    Frame *get_frame(int index = 0);
    /** Return the service sub-type (producer, filter, consumer, transition, etc.). */
    mlt_service_type type();
    /** Attach \p filter to this service. */
    int attach(Filter &filter);
    /** Detach \p filter from this service. */
    int detach(Filter &filter);
    /** Return the number of attached filters. */
    int filter_count();
    /** Move the attached filter at position \p from to position \p to. */
    int move_filter(int from, int to);
    /** Return the attached filter at \p index. Caller does not own the result. */
    Filter *filter(int index);
    void set_profile(mlt_profile profile);
    void set_profile(Profile &profile);
};
} // namespace Mlt

#endif
