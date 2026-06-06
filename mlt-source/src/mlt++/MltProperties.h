/**
 * MltProperties.h - MLT Wrapper
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

#ifndef MLTPP_PROPERTIES_H
#define MLTPP_PROPERTIES_H

#include "MltConfig.h"

#include <framework/mlt.h>
#include <stdio.h>

namespace Mlt {
class Event;
class Animation;

/** \brief C++ wrapper for ::mlt_properties.
 *
 * Properties is a combination list/dictionary of name/value pairs and the
 * base class for most MLT objects. It fires a \em property-changed event
 * when a value is modified.
 *
 * \see mlt_properties_s
 */
class MLTPP_DECLSPEC Properties
{
private:
    mlt_properties instance;

public:
    Properties();
    Properties(bool dummy);
    Properties(Properties &properties);
    Properties(const Properties &properties);
    /** Wrap an existing ::mlt_properties without taking ownership. */
    Properties(mlt_properties properties);
    Properties(void *properties);
    /** Load properties from a file. */
    Properties(const char *file);
    virtual ~Properties();
    Properties &operator=(const Properties &properties);
    virtual mlt_properties get_properties();
    int inc_ref();
    int dec_ref();
    int ref_count();
    /** Acquire the recursive mutex protecting this object. */
    void lock();
    /** Release the recursive mutex. */
    void unlock();
    /** Suppress event delivery to \p object. */
    void block(void *object = NULL);
    /** Resume event delivery to \p object. */
    void unblock(void *object = NULL);
    /** Fire a named event manually. */
    int fire_event(const char *event);
    /** Return true if the underlying ::mlt_properties handle is non-null. */
    bool is_valid();
    /** Return the number of name/value pairs stored. */
    int count();
    /** Get a string property value by name. */
    char *get(const char *name);
    /** Get an integer property value by name. */
    int get_int(const char *name);
    /** Get a 64-bit integer property value by name. */
    int64_t get_int64(const char *name);
    /** Get a double property value by name. */
    double get_double(const char *name);
    /** Get opaque data stored by name; \p size receives the byte count. */
    void *get_data(const char *name, int &size);
    void *get_data(const char *name);
    /** Set a string property. */
    int set(const char *name, const char *value);
    /** Set a string property (no environment-variable expansion). */
    int set_string(const char *name, const char *value);
    /** Set an integer property. */
    int set(const char *name, int value);
    /** Set a 64-bit integer property. */
    int set(const char *name, int64_t value);
    /** Set a double property. */
    int set(const char *name, double value);
    /** Store opaque data with an optional destructor and serialiser. */
    int set(const char *name,
            void *value,
            int size,
            mlt_destructor destroy = NULL,
            mlt_serialiser serial = NULL);
    /** Copy all properties whose names begin with \p prefix from \p that. */
    int copy(Properties &that, const char *prefix);
    /** Copy a single named property from \p that. */
    void pass_property(Properties &that, const char *name);
    /** Copy all properties whose names begin with \p prefix from \p that. */
    int pass_values(Properties &that, const char *prefix);
    /** Copy the space-delimited list of named properties from \p that. */
    int pass_list(Properties &that, const char *list);
    /** Parse a "name=value" string and store the result. */
    int parse(const char *namevalue);
    /** Return the property name at positional \p index. */
    char *get_name(int index);
    /** Return the property value at positional \p index as a string. */
    char *get(int index);
    /** Return the property value at positional \p index in the given time format. */
    char *get(int index, mlt_time_format);
    void *get_data(int index, int &size);
    /** Mirror all property changes on this object to \p that. */
    void mirror(Properties &that);
    /** Copy all properties from \p that into this object. */
    int inherit(Properties &that);
    /** Rename property \p source to \p dest. */
    int rename(const char *source, const char *dest);
    /** Write all properties to \p output as "name=value" lines. */
    void dump(FILE *output = stderr);
    void debug(const char *title = "Object", FILE *output = stderr);
    /** Merge properties from a file into this object. */
    void load(const char *file);
    /** Save properties to a file. */
    int save(const char *file);
    /** Register a listener callback for the named event. Caller owns the returned Event. */
    Event *listen(const char *id, void *object, mlt_listener);
    static void delete_event(Event *);
    /** Set up a one-shot wait handle for event \p id. Caller owns the returned Event. */
    Event *setup_wait_for(const char *id);
    void wait_for(Event *, bool destroy = true);
    void wait_for(const char *id);
    /** Return true if all property names are numeric indices (i.e. a sequence). */
    bool is_sequence();
    /** Parse a YAML Tiny file and return a new Properties tree. Caller owns the result. */
    static Properties *parse_yaml(const char *file);
    char *serialise_yaml();
    /** Apply a named preset from the presets directory. */
    int preset(const char *name);
    int set_lcnumeric(const char *locale);
    const char *get_lcnumeric();
    void clear(const char *name);
    bool property_exists(const char *name);

    char *get_time(const char *name, mlt_time_format = mlt_time_smpte_df);
    char *frames_to_time(int, mlt_time_format = mlt_time_smpte_df);
    int time_to_frames(const char *time);

    mlt_color get_color(const char *name);
    int set(const char *name, mlt_color value);
    mlt_color anim_get_color(const char *name, int position, int length = 0);
    int anim_set(const char *name,
                 mlt_color value,
                 int position,
                 int length = 0,
                 mlt_keyframe_type keyframe_type = mlt_keyframe_linear);

    char *anim_get(const char *name, int position, int length = 0);
    int anim_set(const char *name, const char *value, int position, int length = 0);
    int anim_get_int(const char *name, int position, int length = 0);
    int anim_set(const char *name,
                 int value,
                 int position,
                 int length = 0,
                 mlt_keyframe_type keyframe_type = mlt_keyframe_linear);
    double anim_get_double(const char *name, int position, int length = 0);
    int anim_set(const char *name,
                 double value,
                 int position,
                 int length = 0,
                 mlt_keyframe_type keyframe_type = mlt_keyframe_linear);

    int set(const char *name, mlt_rect value);
    int set(const char *name, double x, double y, double w, double h, double opacity = 1.0);
    mlt_rect get_rect(const char *name);
    int anim_set(const char *name,
                 mlt_rect value,
                 int position,
                 int length = 0,
                 mlt_keyframe_type keyframe_type = mlt_keyframe_linear);
    mlt_rect anim_get_rect(const char *name, int position, int length = 0);
    mlt_animation get_animation(const char *name);
    Animation *get_anim(const char *name);
    bool is_anim(const char *name);

    int set(const char *name, Properties &properties);
    Properties *get_props(const char *name);
    Properties *get_props_at(int index);
};
} // namespace Mlt

#endif
