/**
 * MltField.h - Field wrapper
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

#ifndef MLTPP_FIELD_H
#define MLTPP_FIELD_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltService.h"

namespace Mlt {
class Service;
class Filter;
class Transition;

/** \brief C++ wrapper for ::mlt_field — manages transitions and filters for a Tractor.
 *
 * A field maintains the ordered list of transitions and filters that are
 * applied across the tracks of a Tractor. Obtain it via Tractor::field().
 *
 * \extends Service
 * \see mlt_field_s
 */
class MLTPP_DECLSPEC Field : public Service
{
private:
    mlt_field instance;

public:
    /** Wrap an existing ::mlt_field handle. */
    Field(mlt_field field);
    Field(Field &field);
    virtual ~Field();
    mlt_field get_field();
    mlt_service get_service() override;
    /** Attach \p filter to \p track. */
    int plant_filter(Filter &filter, int track = 0);
    /** Attach \p transition between tracks \p a_track and \p b_track. */
    int plant_transition(Transition &transition, int a_track = 0, int b_track = 1);
    /** Remove \p service from the field. */
    void disconnect_service(Service &service);
};
} // namespace Mlt

#endif
