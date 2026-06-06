/**
 * MltRepository.h - MLT Wrapper
 * Copyright (C) 2008-2026 Meltytech, LLC
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

#ifndef MLTPP_REPOSITORY_H
#define MLTPP_REPOSITORY_H

#include "MltConfig.h"

#ifdef SWIG
#define MLTPP_DECLSPEC
#endif

#include <framework/mlt.h>

namespace Mlt {
class Profile;
class Properties;

/** \brief C++ wrapper for ::mlt_repository — the plugin service registry.
 *
 * The repository is initialised once via Factory::init() and holds all
 * registered producer, filter, transition, and consumer services. It provides
 * metadata queries and service instantiation.
 *
 * \see mlt_repository_s
 */
class MLTPP_DECLSPEC Repository
{
private:
    mlt_repository instance;
    Repository() {}

public:
    Repository(const char *directory);
    Repository(mlt_repository repository);
    ~Repository();

    void register_service(mlt_service_type service_type,
                          const char *service,
                          mlt_register_callback symbol);
    void *create(Profile &profile, mlt_service_type type, const char *service, void *arg);
    /** Return registered consumer service names. Caller owns the result. */
    Properties *consumers() const;
    /** Return registered filter service names. Caller owns the result. */
    Properties *filters() const;
    /** Return registered link service names. Caller owns the result. */
    Properties *links() const;
    /** Return registered producer service names. Caller owns the result. */
    Properties *producers() const;
    /** Return registered transition service names. Caller owns the result. */
    Properties *transitions() const;
    void register_metadata(mlt_service_type type,
                           const char *service,
                           mlt_metadata_callback,
                           void *callback_data);
    /** Return the metadata Properties for \p service of \p type. Caller owns the result. */
    Properties *metadata(mlt_service_type type, const char *service) const;
    /** Return available languages. Caller owns the result. */
    Properties *languages() const;
    /** Return preset names. Caller owns the result. */
    static Properties *presets();
};
} // namespace Mlt

#endif
