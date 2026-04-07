# Rhino 8 Interactive Cheat Sheet Manual

Converted from [Rhino 8 Interactive Cheat Sheet Manual.pdf](https://github.com/jamartinot/rhino8-hotkeys-interactive-html/blob/main/ngrok_tunneling_this_has_port_to_INTERNET/Rhino%208%20Interactive%20Cheat%20Sheet%20Manual.pdf).
 hosted version can be found in: [https://extraterritorial-carlota-ironfisted.ngrok-free.dev/Rhino8_cheat_sheet_timestamps_interactive.html](https://github.com/jamartinot/rhino8-hotkeys-interactive-html/blob/main/ngrok_tunneling_this_has_port_to_INTERNET/Rhino%208%20Interactive%20Cheat%20Sheet%20Manual.pdf)

## Overview


This document describes a single-file Rhino 8 cheat sheet web app. It combines an embedded YouTube video with a clickable command list, responsive layout, theme controls, print-friendly styles, a help popup for video failures, and a few hidden easter eggs.

## Main Features

![Rhino 8 cheat sheet screenshot](assets/Screenshot%202026-04-07%20013947.png)

### Interactive Video Control

The page uses the YouTube IFrame Player API. Clicking a timestamp calculates the target time and jumps the video directly to that moment.

### Responsive Sticky Layout

The layout adapts to screen size with CSS Flexbox and Grid. On mobile, the video sits at the top. On larger screens, the video stays sticky on the left while the command list scrolls on the right.

### Dynamic Theming and Sizing

A settings menu can toggle dark mode by adding a CSS class to the body. A slider adjusts a CSS variable to widen or narrow the video panel.

### Failure Recovery and Help

If YouTube cannot load, the page shows a helper popup with hosted-site and manual-PDF links. After dismissing the popup in an unresolved failure state, the layout can hide the video panel and switch to a more practical reading layout.

### Chat Helpers and Easter Eggs

The chat input supports clean hyperlinks, `host` / `host popup` helper commands, and a couple of comment-blocked easter eggs that can be removed easily if desired.

### Print Optimization

The print stylesheet hides the video, dark background, and interactive controls so the page becomes a clean paper reference.

## Getting Started

Because the video is controlled through the YouTube IFrame API, the file should be served from a local or public web server. Opening it directly from `file:///` can trigger YouTube security errors.

### Method A: Visual Studio Code

1. Install the Live Server extension.
2. Open the HTML file in VS Code.
3. Right-click in the editor and choose Open with Live Server.
4. The browser opens automatically and the video works normally.

### Method B: CodePen

1. Go to CodePen.
2. Paste the HTML into the editor.
3. The preview renders immediately.

### Method C: Python Local Server

1. Open Terminal or Command Prompt.
2. Change into the folder that contains the HTML file.
3. Run `python -m http.server`.
4. Open `http://localhost:8000` in a browser and select the file.

### Method D: ngrok

If you already have a local server, ngrok can expose it temporarily to the internet.

1. Start the local server, for example with `python -m http.server 8000`.
2. In another terminal, run `ngrok http 8000`.
3. Share the public ngrok URL.

### Method E: Permanent Hosting

Upload the file to a public hosting service such as Vercel, Netlify, or a traditional web host. The file must be accessible over HTTP or HTTPS for the video to work correctly.

## Code Structure

The app is organized into three standard web sections: `style` for design, `body` for structure, and `script` for behavior.

### CSS Styling

- CSS variables such as `--bg-color` and `--accent` make global theme changes simple.
- The `body.dark-theme` rules override the default palette for dark mode.
- The `.video-panel` uses `position: sticky; top: 0;` so the video remains visible while scrolling.
- Keyboard keys, mouse icons, and timestamp pills use dedicated classes like `kbd`, `.mouse-action`, and `.ts`.
- A desktop media query switches the main container from stacked to side-by-side layout once the screen is wide enough.

### HTML Structure

- Invisible SVG definitions store reusable mouse icons.
- The main content lives inside a `.layout-container` split into a `.video-panel` and a `.content-panel`.
- Timestamp links use a `data-time` attribute such as `02:34`; the visible text is for the user, while the data attribute is for the script.
- The modal window contains the Info, Settings, and Chat tabs and starts hidden.
- The failure popup includes quick links back to the hosted page, the manual PDF, and a CodePen guide.

### JavaScript Logic

- `onYouTubeIframeAPIReady()` creates the YouTube player in the designated container.
- `parseTimeStr()` converts a timestamp string into seconds.
- Clicking a `.ts` timestamp prevents the default link behavior, reads `data-time`, and calls `seekTo()` on the player.
- Menu and tab event listeners switch the modal open and show the selected tab content.
- Dark mode toggles by adding or removing the `.dark-theme` class on the `body` element.
- The video width slider updates the `--video-width` CSS variable.
- The chat feature appends the user message, then inserts a canned bot reply after a short delay.
- The chat parser supports safe raw links and labeled links like `[Manual PDF](https://...)`.
- `host` opens helper links in chat, while `host popup` opens the failure helper popup.
- The page can fall back to a video-free layout after a failure popup is dismissed.
- Hidden easter eggs live in one clearly marked block so they can be commented out without touching the rest of the script.

## Customization Guide

### Change the Video

Edit the `videoId` in `onYouTubeIframeAPIReady()` to point to a different YouTube video.

```javascript
function onYouTubeIframeAPIReady() {
    player = new YT.Player('player', {
        videoId: 'NEW_VIDEO_ID_HERE',
        playerVars: { 'playsinline': 1, 'rel': 0 }
    });
}
```

### Add a New Command

Copy an existing list item and replace the command name, description, and timestamp.

```html
<li>
    <span class="cmd-type">YourCommand</span>
    <span class="desc">What it does</span>
    <a class="ts" data-time="12:34">12:34</a>
</li>
```

The `data-time` value can use `MM:SS` or `H:MM:SS` formats.

### Change Colors

Update the CSS variables in `:root` and, if needed, the matching values inside `body.dark-theme`.

```css
:root {
    --primary: #1f2937;
    --accent: #2563eb;
    --bg-light: #f9fafb;
}
```

### Adjust Video Size

Change the default `--video-width` value and update the `videoSizeSlider` input so the control matches the new default.

```html
<input type="range" id="videoSizeSlider" min="40" max="80" value="40">
```

### Modify Keyboard and Mouse Icons

Wrap keyboard shortcuts in `kbd` tags and reuse the mouse SVG snippets for button icons.

### Resize the Menu Window

Adjust the `.modal` class width and height to make the popup larger or smaller.

### Change Page Width and Margins

The body `max-width` and padding control how much horizontal space the app occupies.

### Use a Two-Column Layout

The `.grid-2col` settings control whether content can be displayed in a one-column or two-column grid.

### Change the Chat Bot Reply

Edit the `botResponse` string in `handleSend()` to change the automated reply text.

### Remove the Easter Eggs

Comment out the clearly marked `EASTER EGGS` block in the script to disable the hidden `egg` and `rhino` commands without affecting the rest of the page.

## Contact

Issues with the manual can be sent to jairmarttila725 at gmail dot com.
