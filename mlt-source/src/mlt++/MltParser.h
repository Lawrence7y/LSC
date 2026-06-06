/**
 * MltParser.h - MLT Wrapper
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

#ifndef MLTPP_PARSER_H
#define MLTPP_PARSER_H

#include "MltConfig.h"

#include "MltProperties.h"
#include <framework/mlt.h>

namespace Mlt {
class Chain;
class Filter;
class Link;
class Multitrack;
class Playlist;
class Producer;
class Properties;
class Service;
class Tractor;
class Transition;

/** \brief C++ wrapper for ::mlt_parser — walks a service network and fires callbacks.
 *
 * Subclass Parser and override the virtual on_start and on_end methods to
 * inspect or transform every node in a service graph(producers, playlists, *tractors, filters, transitions, chains, links).
 * Call start() to begin traversal.
 *
 * \extends Properties
 * \see mlt_parser_s
 */
class MLTPP_DECLSPEC Parser : public Properties
{
private:
    mlt_parser parser;

public:
    Parser();
    ~Parser();
    int start(Service &service);
    virtual mlt_properties get_properties() override;
    virtual int on_invalid(Service *object);
    virtual int on_unknown(Service *object);
    virtual int on_start_producer(Producer *object);
    virtual int on_end_producer(Producer *object);
    virtual int on_start_playlist(Playlist *object);
    virtual int on_end_playlist(Playlist *object);
    virtual int on_start_tractor(Tractor *object);
    virtual int on_end_tractor(Tractor *object);
    virtual int on_start_multitrack(Multitrack *object);
    virtual int on_end_multitrack(Multitrack *object);
    virtual int on_start_track();
    virtual int on_end_track();
    virtual int on_start_filter(Filter *object);
    virtual int on_end_filter(Filter *object);
    virtual int on_start_transition(Transition *object);
    virtual int on_end_transition(Transition *object);
    virtual int on_start_chain(Chain *object);
    virtual int on_end_chain(Chain *object);
    virtual int on_start_link(Link *object);
    virtual int on_end_link(Link *object);
};
} // namespace Mlt

#endif
