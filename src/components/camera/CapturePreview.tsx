import { CircleCheck, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { CaptureQualityWarning } from "@/lib/camera/quality";

/**
 * Quick preview of the photo just taken. Without quality warnings it is
 * shown briefly and the camera advances automatically; with warnings the
 * employee decides (future quality checks).
 */
export function CapturePreview({
  url,
  title,
  warnings,
  onAccept,
  onRetake,
}: {
  url: string;
  title: string;
  warnings: readonly CaptureQualityWarning[];
  onAccept: () => void;
  onRetake: () => void;
}) {
  return (
    <div className="absolute inset-0 z-20 flex animate-fade-in flex-col bg-black">
      <img src={url} alt={`Aufgenommenes Foto: ${title}`} className="min-h-0 flex-1 object-contain" />
      <div className="flex flex-col items-center gap-2 p-3">
        {warnings.length === 0 ? (
          <p className="flex items-center gap-2 rounded-full bg-ae-success/20 px-3.5 py-1.5 text-sm font-semibold text-ae-success" role="status">
            <CircleCheck className="size-4" aria-hidden />
            Foto aufgenommen
          </p>
        ) : (
          <>
            <ul className="w-full max-w-sm space-y-1.5">
              {warnings.map((warning) => (
                <li
                  key={warning.code}
                  className="flex items-start gap-2 rounded-lg bg-ae-warning/15 px-3 py-2 text-sm text-ae-warning"
                >
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                  {warning.message}
                </li>
              ))}
            </ul>
            <div className="grid w-full max-w-sm grid-cols-2 gap-2">
              <Button variant="secondary" onClick={onRetake}>
                Foto wiederholen
              </Button>
              <Button onClick={onAccept}>Trotzdem verwenden</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
