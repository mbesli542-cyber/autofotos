import { CameraOff, ImageUp, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { CAMERA_ERROR_MESSAGES, type CameraErrorCode } from "@/lib/camera/camera-stream";

/** Shown instead of the live preview when the camera cannot be used. */
export function CameraErrorPanel({
  code,
  onRetry,
  onUpload,
}: {
  code: CameraErrorCode;
  onRetry: () => void;
  onUpload: () => void;
}) {
  const message = CAMERA_ERROR_MESSAGES[code];
  const canRetry = code !== "insecure_context" && code !== "not_supported";
  return (
    <div
      role="alert"
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-black/80 p-6 text-center backdrop-blur-sm"
    >
      <CameraOff className="size-9 text-white/70" aria-hidden />
      <div>
        <p className="font-semibold text-white">{message.title}</p>
        <p className="mt-1 max-w-xs text-sm text-white/70">{message.description}</p>
      </div>
      <div className="mt-2 flex flex-wrap justify-center gap-2">
        {canRetry && (
          <Button variant="secondary" onClick={onRetry} icon={<RefreshCw className="size-4" aria-hidden />}>
            Erneut versuchen
          </Button>
        )}
        <Button onClick={onUpload} icon={<ImageUp className="size-4" aria-hidden />}>
          Foto hochladen
        </Button>
      </div>
    </div>
  );
}
