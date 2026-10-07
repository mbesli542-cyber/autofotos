import { CAMERA_CONFIG } from "@/lib/camera/config";
import type { ShotDefinition } from "@/lib/shots/shot-template";

/**
 * Framing guide on top of the live preview: subtle thirds grid, corner marks
 * and the semi-transparent vehicle outline for the current shot.
 * Opacity is configurable (CAMERA_CONFIG.overlayOpacity).
 */
export function CameraOverlay({
  shot,
  opacity = CAMERA_CONFIG.overlayOpacity,
  showGrid = CAMERA_CONFIG.showGrid,
}: {
  shot: ShotDefinition;
  opacity?: number;
  showGrid?: boolean;
}) {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {showGrid && (
        <svg className="absolute inset-0 size-full" viewBox="0 0 300 300" preserveAspectRatio="none">
          <g stroke="white" strokeOpacity="0.18" strokeWidth="1" vectorEffect="non-scaling-stroke">
            <line x1="100" y1="0" x2="100" y2="300" vectorEffect="non-scaling-stroke" />
            <line x1="200" y1="0" x2="200" y2="300" vectorEffect="non-scaling-stroke" />
            <line x1="0" y1="100" x2="300" y2="100" vectorEffect="non-scaling-stroke" />
            <line x1="0" y1="200" x2="300" y2="200" vectorEffect="non-scaling-stroke" />
          </g>
        </svg>
      )}

      {/* Corner marks */}
      {[
        "top-3 left-3 border-t-2 border-l-2 rounded-tl-lg",
        "top-3 right-3 border-t-2 border-r-2 rounded-tr-lg",
        "bottom-3 left-3 border-b-2 border-l-2 rounded-bl-lg",
        "right-3 bottom-3 border-r-2 border-b-2 rounded-br-lg",
      ].map((position) => (
        <span key={position} className={`absolute size-7 border-white/70 ${position}`} />
      ))}

      {shot.overlayAsset && (
        <img
          key={`${shot.key}-${shot.overlayAsset}`}
          src={shot.overlayAsset}
          alt=""
          className="absolute inset-0 size-full animate-fade-in object-contain"
          style={{
            opacity,
            transform: shot.overlayMirrored ? "scaleX(-1)" : undefined,
            // Blue glow like the AutoExperten guide lines.
            filter: "drop-shadow(0 0 6px rgb(10 123 255 / 0.9)) drop-shadow(0 0 2px rgb(120 190 255 / 0.9))",
          }}
          draggable={false}
        />
      )}
    </div>
  );
}
