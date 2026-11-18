# Mascot spritesheets

AstroMeet ships two characters, Astro and Brasa (`src/teams_lite/assets/mascots/`). Your own drawings
replace a shipped character, state by state, or add a new one without touching code: put them in a
character folder and pick the character in **Mascota**. A state with no drawing anywhere uses a simple
code-drawn placeholder.

## Where the files go

```text
data/mascots/
  astro/            one folder per character (letters, digits, "_" or "-")
    astro_idle.png
    astro_moving.png
    ...
  brasa/
    brasa_idle.png  (or idle.png, or astro_idle.png)
    ...
```

*Mascota › Abrir carpeta de personajes* opens `data/mascots/`. A character appears in the list as soon
as its folder holds a PNG. A state without a file, or with a file that breaks the rules below, keeps
the placeholder for that state, and the Mascota section says which file could not be used.

## The eight states

| File | State | When it shows | Loop |
|------|-------|---------------|------|
| `<name>_idle.png` | IDLE | Nothing is recording: floats, blinks now and then. | yes |
| `<name>_moving.png` | MOVING | While the mascot is dragged. | yes |
| `<name>_recording.png` | RECORDING | During the whole recording: focused face, red indicator, audio waves in the headphones. | yes |
| `<name>_stopping.png` | STOPPING | From "Detener" until the audio is stored. | yes |
| `<name>_capturing.png` | CAPTURING | About 2 s after a screenshot or an OCR read succeeded (camera flash). | yes |
| `<name>_processing.png` | PROCESSING | While the recording is transcribed. | yes |
| `<name>_success.png` | SUCCESS | About 3.5 s after the meeting is stored or transcribed. | yes |
| `<name>_error.png` | ERROR | About 6 s after something failed (worried face, warning sign). | yes |

## Rules for each PNG

- **One horizontal strip** of frames: frame 1, frame 2, … left to right, no gaps.
- **Square frames**: the image height is the frame size and the width must be a whole multiple of it
  (for example 4 frames of 64 px = 256 × 64).
- **Transparent background** (PNG with alpha).
- **Same frame size, scale and position** of the character in every file and every frame, so the
  character does not jump when the state changes. 32, 48 or 64 px frames work well.
- Any number of frames; every state loops. Playback speed: idle 4 fps, error 4, recording 5,
  stopping, processing and success 6, moving and capturing 8.
- Draw at the final pixel size: AstroMeet scales by whole numbers with no smoothing
  (Pequeña ×2, Mediana ×3, Grande ×4, Muy grande ×5), so pixels stay sharp.

## Prompt: Astro

> Pixel-art spritesheet, classic 16-bit game style, transparent background. Character: **Astro**, a
> small floating astral dragon, chibi proportions, blue, violet and light-blue body with golden
> details, small wings, golden horns, a gold star on the head, a cosmic swirling tail, white belly,
> big expressive eyes, gaming headphones with a microphone. Same character, scale, palette and
> position in every frame. One horizontal strip of N square frames of 64×64 px, no gaps, for the
> animation **{STATE}**: {DESCRIPTION}. Loopable. No text, no background, no UI.

Replace `{STATE}` and `{DESCRIPTION}`:

| State | Description |
|-------|-------------|
| idle | floating gently up and down, blinks once, wings and tail sway slightly, calm smile |
| moving | flying sideways, flapping wings, a short sparkle trail from the tail |
| recording | focused face, a pulsing red record dot next to it, audio wave bars on the headphones |
| stopping | presses a red stop button, the red dot fades out, closes the eyes briefly |
| capturing | holds a holographic camera, one bright flash frame |
| processing | looks at a small hologram, three dots or small symbols cycling above |
| success | big smile, wings raised, a gold star and a green check appear |
| error | worried face, wings lowered, a yellow warning sign blinking |

## Prompt: an original fire dragon ("Brasa")

Use your own design, not an existing character: avoid well-known franchise looks (for example an
orange dragon with teal wing membranes, cream belly and a flame on the tip of the tail).

> Pixel-art spritesheet, classic 16-bit game style, transparent background. Character: **Brasa**, an
> original chubby ember salamander-dragon, round body standing on four short legs, deep crimson and
> charcoal skin with glowing gold cracks like cooling lava, two small rounded horns, tiny leaf-shaped
> wings the color of soot with ember edges, short tail ending in a round glowing ember stone (no
> flame), round amber eyes, gaming headphones with a microphone. Same character, scale, palette and
> position in every frame. One horizontal strip of N square frames of 64×64 px, no gaps, for the
> animation **{STATE}**: {DESCRIPTION}. Loopable. No text, no logos, no background, no UI.

Use the same state table as Astro, and save the files in `data/mascots/brasa/`.
