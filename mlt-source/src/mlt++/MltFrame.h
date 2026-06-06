/**
 * MltFilter.h - MLT Wrapper
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

#ifndef MLTPP_FRAME_H
#define MLTPP_FRAME_H

#include "MltConfig.h"

#include "MltProperties.h"
#include <framework/mlt.h>

namespace Mlt {
class Properties;
class Producer;
class Service;

/** \brief C++ wrapper for ::mlt_frame — a single unit of audio/video data.
 *
 * A frame carries all data for one rendered moment: pixel buffer, audio
 * samples, and per-frame properties. Frames are obtained from a service via
 * Service::get_frame() and are owned by the caller.
 *
 * \extends Properties
 * \see mlt_frame_s
 */
class MLTPP_DECLSPEC Frame : public Properties
{
private:
    mlt_frame instance;

public:
    Frame();
    /** Wrap an existing ::mlt_frame handle. */
    Frame(mlt_frame frame);
    Frame(Frame &frame);
    Frame(const Frame &frame);
    virtual ~Frame();
    Frame &operator=(const Frame &frame);
    virtual mlt_frame get_frame();
    mlt_properties get_properties() override;
    /** Decode and return the pixel buffer in the requested \p format.
     *  On return \p w and \p h contain the actual dimensions. */
    uint8_t *get_image(mlt_image_format &format, int &w, int &h, int writable = 0);
    /** Scale the image to exactly \p w x \p h before returning. */
    unsigned char *fetch_image(mlt_image_format format, int w, int h, int writable = 0);
    /** Decode and return raw audio samples. */
    void *get_audio(mlt_audio_format &format, int &frequency, int &channels, int &samples);
    /** Render a waveform image of size \p w x \p h. Caller owns result. */
    unsigned char *get_waveform(int w, int h);
    /** Return the producer that originally created this frame. Caller owns the result. */
    Producer *get_original_producer();
    /** Return the absolute frame position. */
    int get_position();
    /** Return the per-frame property bag for \p service (thread-safe). */
    mlt_properties get_unique_properties(Service &service);
    int set_image(uint8_t *image, int size, mlt_destructor destroy);
    int set_alpha(uint8_t *alpha, int size, mlt_destructor destroy);
};
} // namespace Mlt

#endif
