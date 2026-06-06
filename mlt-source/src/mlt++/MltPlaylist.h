/**
 * MltPlaylist.h - MLT Wrapper
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

#ifndef MLTPP_PLAYLIST_H
#define MLTPP_PLAYLIST_H

#include "MltConfig.h"

#include <framework/mlt.h>

#include "MltProducer.h"

namespace Mlt {
class Producer;
class Service;
class Playlist;
class Transition;
class Profile;

/** \brief Metadata for a single clip within a Playlist.
 *
 * Populated by Playlist::clip_info(). The \p producer and \p cut fields
 * are owned by the ClipInfo object.
 */
class MLTPP_DECLSPEC ClipInfo
{
public:
    ClipInfo();
    ClipInfo(mlt_playlist_clip_info *info);
    ~ClipInfo();
    void update(mlt_playlist_clip_info *info);
    int clip;
    Producer *producer;
    Producer *cut;
    int start;
    char *resource;
    int frame_in;
    int frame_out;
    int frame_count;
    int length;
    float fps;
    int repeat;
};

/** \brief C++ wrapper for ::mlt_playlist — a sequential list of clips.
 *
 * A playlist is a producer that plays its clips in order. Clips are
 * producers (or cut-producers) that may be appended, inserted, removed,
 * and trimmed. Blank gaps between clips are also supported.
 *
 * \extends Producer
 * \see mlt_playlist_s
 */
class MLTPP_DECLSPEC Playlist : public Producer
{
private:
    mlt_playlist instance;

public:
    Playlist();
    Playlist(Profile &profile);
    Playlist(Service &playlist);
    Playlist(Playlist &playlist);
    /** Wrap an existing ::mlt_playlist handle. */
    Playlist(mlt_playlist playlist);
    virtual ~Playlist();
    virtual mlt_playlist get_playlist();
    mlt_producer get_producer() override;
    /** Return the number of entries (clips and blanks). */
    int count();
    /** Remove all entries from the playlist. */
    int clear();
    /** Append \p producer (optionally trimmed to [\p in, \p out]) to the end. */
    int append(Producer &producer, int in = -1, int out = -1);
    /** Append a blank of \p out + 1 frames. */
    int blank(int out);
    /** Append a blank of the given timecode \p length. */
    int blank(const char *length);
    int clip(mlt_whence whence, int index);
    /** Return the index of the currently playing clip. */
    int current_clip();
    /** Return the currently playing producer. Caller owns the result. */
    Producer *current();
    /** Return metadata for the clip at \p index. When \p info is NULL a new ClipInfo is
     *  returned and the caller owns it; otherwise \p info is updated in-place and returned. */
    ClipInfo *clip_info(int index, ClipInfo *info = NULL);
    static void delete_clip_info(ClipInfo *info);
    /** Insert \p producer at position \p where, shifting later clips. */
    int insert(Producer &producer, int where, int in = -1, int out = -1);
    /** Remove the clip at position \p where. */
    int remove(int where);
    /** Move the clip at \p from to \p to. */
    int move(int from, int to);
    /** Reorder the playlist using an array of old indices. */
    int reorder(const int *indices);
    /** Retrim the clip at \p clip to [\p in, \p out]. */
    int resize_clip(int clip, int in, int out);
    /** Split the clip at \p clip at frame \p position (relative to clip start). */
    int split(int clip, int position);
    /** Split at absolute timeline \p position. */
    int split_at(int position, bool left = true);
    /** Join \p count clips starting at \p clip into one. */
    int join(int clip, int count = 1, int merge = 1);
    /** Create a mix (dissolve) of \p length frames at the \p clip boundary. */
    int mix(int clip, int length, Transition *transition = NULL);
    int mix_in(int clip, int length);
    int mix_out(int clip, int length);
    /** Add a transition to an existing mix at \p clip. */
    int mix_add(int clip, Transition *transition);
    /** Repeat the clip at \p clip \p count additional times. */
    int repeat(int clip, int count);
    /** Return the producer for clip \p clip. Caller owns the result. */
    Producer *get_clip(int clip);
    /** Return the producer at absolute timeline frame \p position. Caller owns the result. */
    Producer *get_clip_at(int position);
    /** Return the clip index at absolute timeline frame \p position. */
    int get_clip_index_at(int position);
    /** Return true if the clip at \p clip is a mix. */
    bool is_mix(int clip);
    /** Return true if the clip at \p clip is a blank. */
    bool is_blank(int clip);
    /** Return true if the absolute timeline \p position is in a blank. */
    bool is_blank_at(int position);
    /** Merge adjacent blanks; if \p keep_length is 0, trim trailing blanks. */
    void consolidate_blanks(int keep_length = 0);
    /** Replace the clip at \p clip with a blank of the same length. Caller owns the result. */
    Producer *replace_with_blank(int clip);
    void insert_blank(int clip, int out);
    void pad_blanks(int position, int length, int find = 0);
    /** Insert \p producer at absolute timeline \p position. */
    int insert_at(int position, Producer *producer, int mode = 0);
    int insert_at(int position, Producer &producer, int mode = 0);
    /** Return the start frame of clip \p clip in the timeline. */
    int clip_start(int clip);
    int clip_length(int clip);
    int blanks_from(int clip, int bounded = 0);
    int remove_region(int position, int length);
};
} // namespace Mlt

#endif
