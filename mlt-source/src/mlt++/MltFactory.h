/**
 * MltFactory.h - MLT Wrapper
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

#ifndef MLTPP_FACTORY_H
#define MLTPP_FACTORY_H

#include "MltConfig.h"

#ifdef SWIG
#define MLTPP_DECLSPEC
#endif

#include <framework/mlt.h>

namespace Mlt {
class Properties;
class Producer;
class Filter;
class Transition;
class Consumer;
class Profile;
class Repository;

/** \brief Static factory for creating MLT services.
 *
 * Call Factory::init() once at application start to load all plugins from
 * the MLT_REPOSITORY directory. Use the static factory methods to create
 * producers, filters, transitions, and consumers by service identifier.
 *
 * \see mlt_factory_s
 */
class MLTPP_DECLSPEC Factory
{
public:
    /** Initialise the factory from \p directory. Caller owns the returned Repository. */
    static Repository *init(const char *directory = NULL);
    /** Return the global event object. Caller owns the result. */
    static Properties *event_object();
    /** Instantiate a producer by \p id. Caller owns the result. */
    static Producer *producer(Profile &profile, char *id, char *arg = NULL);
    /** Instantiate a filter by \p id. Caller owns the result. */
    static Filter *filter(Profile &profile, char *id, char *arg = NULL);
    /** Instantiate a transition by \p id. Caller owns the result. */
    static Transition *transition(Profile &profile, char *id, char *arg = NULL);
    /** Instantiate a consumer by \p id. Caller owns the result. */
    static Consumer *consumer(Profile &profile, char *id, char *arg = NULL);
    static void close();
};
} // namespace Mlt

#endif
