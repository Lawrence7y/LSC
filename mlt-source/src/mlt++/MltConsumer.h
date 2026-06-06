/**
 * MltConsumer.h - MLT Wrapper
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

#ifndef MLTPP_CONSUMER_H
#define MLTPP_CONSUMER_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltService.h"

namespace Mlt {
class Service;
class Profile;

/** \brief C++ wrapper for ::mlt_consumer — pulls and outputs audio/video.
 *
 * A consumer pulls frames from the service network and renders them to a
 * device, file, or socket. It drives the rendering pipeline. Connect a
 * producer (or tractor/playlist) with connect(), then call start().
 *
 * \extends Service
 * \see mlt_consumer_s
 */
class MLTPP_DECLSPEC Consumer : public Service
{
private:
    mlt_consumer instance;

public:
    Consumer();
    Consumer(Profile &profile);
    /** Construct and instantiate consumer \p id from the repository. */
    Consumer(Profile &profile, const char *id, const char *service = NULL);
    Consumer(mlt_profile profile, const char *id, const char *service = NULL);
    Consumer(Service &consumer);
    Consumer(Consumer &consumer);
    /** Wrap an existing ::mlt_consumer handle. */
    Consumer(mlt_consumer consumer);
    virtual ~Consumer();
    virtual mlt_consumer get_consumer();
    mlt_service get_service() override;
    /** Connect \p service as the source for this consumer. */
    virtual int connect(Service &service);
    /** Start rendering synchronously (returns when stopped). */
    int run();
    /** Start rendering asynchronously in a background thread. */
    int start();
    /** Flush buffered frames. */
    void purge();
    /** Stop the rendering thread and wait for it to finish. */
    int stop();
    /** Return true if the consumer is not currently rendering. */
    bool is_stopped();
    /** Return the current output frame position. */
    int position();
};
} // namespace Mlt

#endif
