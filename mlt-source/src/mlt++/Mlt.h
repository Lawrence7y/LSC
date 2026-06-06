/**
 * Mlt.h - Convenience header file for all mlt++ objects
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

/** \defgroup mltpp MLT C++ Wrappers
 *  \brief High-level C++ wrappers around the MLT framework C API.
 *
 *  Include this header to pull in all mlt++ classes. The classes live in
 *  the \c Mlt namespace and wrap the corresponding \c mlt_*_s C structs from
 *  \c src/framework/.
 */

#ifndef MLTPP_H
#define MLTPP_H

#include "MltAnimation.h"
#include "MltAudio.h"
#include "MltChain.h"
#include "MltConsumer.h"
#include "MltDeque.h"
#include "MltEvent.h"
#include "MltFactory.h"
#include "MltField.h"
#include "MltFilter.h"
#include "MltFilteredConsumer.h"
#include "MltFrame.h"
#include "MltImage.h"
#include "MltMultitrack.h"
#include "MltParser.h"
#include "MltPlaylist.h"
#include "MltProducer.h"
#include "MltProfile.h"
#include "MltProperties.h"
#include "MltPushConsumer.h"
#include "MltRepository.h"
#include "MltService.h"
#include "MltTokeniser.h"
#include "MltTractor.h"
#include "MltTransition.h"

#endif
