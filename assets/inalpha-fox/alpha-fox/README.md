# Inalpha Alpha Fox

Native Rive reconstruction of the approved Alpha Fox concept in
`reference/approved-alpha.png`: an alpha-shaped espresso fox, ivory face,
vermilion tail, and dark eyes with small negative-space notches. Flat native
Bezier shapes replace the concept sheet's raster texture. The reference bitmap
is not embedded in the runtime.

## Preview

```sh
rive assets/inalpha-fox/alpha-fox --data=backgroundOpacity=1 --fit=contain
```

Move the pointer anywhere inside the 600 × 500 artboard. The eyes move up to
7.2 px horizontally and 5.4 px vertically, plus small face parallax. Attention
blends in and returns to neutral over 220 ms on pointer exit. The alpha body
stays fixed. A twelve-second idle includes unequal blink intervals, a double blink, gentle
vertical breathing motion and two brief curious tilts (under 1.6 degrees).
Short tail swishes alternate with quiet motion. Clicking inside the artboard
plays a 1.4-second anticipation, small lift, soft squint and tail flourish;
it returns to idle and can be triggered again. Repeated clicks during that
response are ignored, avoiding stacked motion. Touch moves can also point
the gaze; idle animation works without a pointer.

The preview background is optional: the exported file defaults to transparent.
`AlphaFox.backgroundOpacity` is 0 for transparency and 1 for warm ivory.

## Files

- `scene.rml`: native editable scene, three state-machine layers and seven timelines.
- `build_scene.py` and `geometry.py`: reproducible geometry and animation source.
- `inalpha-alpha-fox.riv`: checked-in runtime file (approximately 9.8 KB).
- `build/inalpha-alpha-fox-lively.gif`: twelve-second native-rendered demonstration;
  pointer movement and one click are simulated in the capture, so the GIF itself is not interactive.
- `render_preview.py`: native pointer/loop checks and GIF capture using Pillow.

## Build and validate

```sh
python3 assets/inalpha-fox/alpha-fox/build_scene.py
rive assets/inalpha-fox/alpha-fox --verify
rive inspect assets/inalpha-fox/alpha-fox --summary
rive assets/inalpha-fox/alpha-fox --once
cp assets/inalpha-fox/alpha-fox/build/inalpha-alpha-fox.riv assets/inalpha-fox/alpha-fox/inalpha-alpha-fox.riv
python3 assets/inalpha-fox/alpha-fox/render_preview.py
```

Use Rive CLI 1.8.0 or a compatible newer version. The native captures check
left/right and vertical gaze, return to centre after exit, first and repeated
click responses, and a matching loop
seam. Closed-eye and tail-extreme frames are also rendered for visual review.

## Website integration contract

Artboard: `Inalpha Alpha Fox`. State machine: `Alpha Fox`. Bind the default
`AlphaFox` view model instance. Pointer listeners, a joystick, constraints and
state transitions implement interaction entirely inside Rive; no embedded
scripts, image assets, fonts or network calls are required. `isHovering` is an
internal boolean driven by pointer listeners. The `delight` trigger starts the
same greeting from host code, for example after a completed research action. Keep `backgroundOpacity=0` for a
transparent web canvas.

This delivery is a local Rive preview and runtime asset. It has not been
integrated into or deployed on the website, and browser-runtime compatibility
has not yet been tested. Website integration should honour reduced-motion
preferences and pause idle animation offscreen. The current dark mark is for
light surfaces; a dark-surface colour variant is not included.

Editable RML is supplied. Exporting an editor `.rev` requires a separate CLI
sign-in (`rive login`); browser sign-in alone does not grant that session.
