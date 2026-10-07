#!/usr/bin/env node
/**
 * Generates the local placeholder photos used by demo mode:
 *   public/demo/shots/{shotKey}.svg
 *
 * They are clearly labelled "DEMO" illustrations built from the camera
 * overlays – no remote images, nothing that can disappear.
 * The shot list mirrors src/lib/shots/shot-template.ts.
 *
 * Usage: npm run generate:demo-assets
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const overlayDir = join(root, "public", "overlays");
const outDir = join(root, "public", "demo", "shots");

const SHOTS = [
  ["front_left_45", 1, "Vorne links (45°)", "front-left-45.svg"],
  ["front", 2, "Vorne", "front.svg"],
  ["front_right_45", 3, "Vorne rechts (45°)", "front-right-45.svg"],
  ["left_side", 4, "Linke Fahrzeugseite", "side.svg"],
  ["right_side", 5, "Rechte Fahrzeugseite", "side.svg", true],
  ["rear_left_45", 6, "Hinten links (45°)", "rear-left-45.svg"],
  ["rear", 7, "Hinten", "rear.svg"],
  ["rear_right_45", 8, "Hinten rechts (45°)", "rear-right-45.svg"],
  ["cockpit", 9, "Cockpit", "cockpit.svg"],
  ["front_interior", 10, "Innenraum vorne", null],
  ["rear_seats", 11, "Fond / Rücksitze", null],
  ["driver_seat", 12, "Fahrersitz", null],
  ["door_controls", 13, "Tür & Sitzbedienung", null],
  ["wheel_detail", 14, "Felge / Rad", "wheel.svg"],
  ["special_detail", 15, "Fahrzeugdetail", null],
];

const W = 1600;
const H = 1200;

function escapeXml(value) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function overlayBody(file, strokeColor) {
  const svg = readFileSync(join(overlayDir, file), "utf8");
  const inner = svg
    .replace(/^[\s\S]*?<svg[^>]*>/, "")
    .replace(/<\/svg>\s*$/, "")
    .replace(/<!--[\s\S]*?-->/g, "");
  return inner
    .replace(/fill="#FFFFFF" fill-opacity="0.07"/g, 'fill="url(#paint)"')
    .replace(/stroke="#FFFFFF"/g, `stroke="${strokeColor}"`);
}

function interiorScene() {
  return `
    <rect x="260" y="180" width="1080" height="640" rx="40" fill="#2b2f36"/>
    <rect x="360" y="300" width="360" height="460" rx="60" fill="#3a3f48"/>
    <rect x="880" y="300" width="360" height="460" rx="60" fill="#3a3f48"/>
    <rect x="400" y="240" width="280" height="90" rx="40" fill="#454b55"/>
    <rect x="920" y="240" width="280" height="90" rx="40" fill="#454b55"/>
    <rect x="740" y="420" width="120" height="340" rx="20" fill="#23272d"/>`;
}

function buildSvg([, order, title, overlay, mirrored]) {
  const isExterior = order <= 8;
  const background = isExterior
    ? `<rect width="${W}" height="${H}" fill="url(#wall)"/>
       <rect y="${H * 0.66}" width="${W}" height="${H * 0.34}" fill="url(#floor)"/>`
    : `<rect width="${W}" height="${H}" fill="url(#interior)"/>`;

  const content = overlay
    ? `<svg x="0" y="${isExterior ? 60 : 100}" width="${W}" height="${W * 0.625}" viewBox="0 0 1600 1000" fill="none">
         <g ${mirrored ? 'transform="translate(1600 0) scale(-1 1)"' : ""}>${overlayBody(overlay, isExterior ? "#1b1f25" : "#c9ced6")}</g>
       </svg>`
    : interiorScene();

  const label = escapeXml(`${String(order).padStart(2, "0")} · ${title}`);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">
  <!-- DEMO-Platzhalterfoto (generiert von scripts/generate-demo-assets.mjs) -->
  <defs>
    <linearGradient id="wall" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#e9ecf0"/><stop offset="1" stop-color="#c9ced6"/>
    </linearGradient>
    <linearGradient id="floor" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#9a8068"/><stop offset="1" stop-color="#6e5643"/>
    </linearGradient>
    <linearGradient id="interior" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1a1d22"/><stop offset="1" stop-color="#2c3139"/>
    </linearGradient>
    <linearGradient id="paint" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#8b939e"/><stop offset="0.55" stop-color="#4a515b"/><stop offset="1" stop-color="#2a2f36"/>
    </linearGradient>
  </defs>
  ${background}
  ${content}
  <g font-family="-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">
    <rect x="40" y="${H - 120}" width="${Math.max(420, label.length * 26 + 190)}" height="80" rx="16" fill="#0b0c0e" fill-opacity="0.82"/>
    <rect x="60" y="${H - 100}" width="110" height="40" rx="8" fill="#0A7BFF"/>
    <text x="115" y="${H - 72}" fill="#fff" font-size="24" font-weight="700" text-anchor="middle">DEMO</text>
    <text x="190" y="${H - 70}" fill="#fff" font-size="34" font-weight="600">${label}</text>
  </g>
</svg>
`;
}

mkdirSync(outDir, { recursive: true });
for (const shot of SHOTS) {
  writeFileSync(join(outDir, `${shot[0]}.svg`), buildSvg(shot));
}
console.log(`Generated ${SHOTS.length} demo shots in public/demo/shots`);
