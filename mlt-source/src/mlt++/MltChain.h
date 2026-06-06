/**
 * MltChain.h - Chain wrapper
 * Copyright (C) 2020-2026 Meltytech, LLC
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

#ifndef MLTPP_CHAIN_H
#define MLTPP_CHAIN_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltLink.h"
#include "MltProducer.h"
#include "MltProfile.h"

namespace Mlt {
/** \brief C++ wrapper for ::mlt_chain — a linear processing pipeline.
 *
 * A chain is a producer that wraps another producer (the source) and applies
 * an ordered sequence of Link objects to it. Links are similar to filters
 * but designed for the chain architecture.
 *
 * \extends Producer
 * \see mlt_chain_s
 */
class MLTPP_DECLSPEC Chain : public Producer
{
private:
    mlt_chain instance;

public:
    Chain();
    /** Construct and instantiate chain \p id from the repository. */
    Chain(Profile &profile, const char *id, const char *service = NULL);
    Chain(Mlt::Profile &profile);
    /** Wrap an existing ::mlt_chain handle. */
    Chain(mlt_chain chain);
    Chain(Chain &chain);
    Chain(Chain *chain);
    Chain(Service &chain);
    virtual ~Chain();
    virtual mlt_chain get_chain();
    mlt_producer get_producer() override;
    /** Set the source producer that this chain wraps. */
    void set_source(Mlt::Producer &source);
    /** Return the wrapped source producer. */
    Mlt::Producer get_source();
    /** Append \p link to the end of this chain. */
    int attach(Mlt::Link &link);
    /** Remove \p link from this chain. */
    int detach(Mlt::Link &link);
    /** Return the number of links in this chain. */
    int link_count() const;
    /** Move the link at position \p from to position \p to. */
    bool move_link(int from, int to);
    /** Return the link at \p index. Caller does not own the result. */
    Mlt::Link *link(int index);
    /** Attach any normalizer links required by the source. */
    void attach_normalizers();
};
} // namespace Mlt

#endif
