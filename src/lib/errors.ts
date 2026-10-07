/**
 * Application errors with German, user-presentable messages.
 * Raw technical errors are never shown to employees.
 */

export type AppErrorCode =
  | "network"
  | "not_found"
  | "unauthorized"
  | "storage"
  | "invalid"
  | "unknown";

export const USER_ERROR_MESSAGES: Record<AppErrorCode, string> = {
  network: "Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.",
  not_found: "Der Eintrag wurde nicht gefunden.",
  unauthorized: "Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.",
  storage: "Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.",
  invalid: "Die Eingaben sind ungültig. Bitte prüfen Sie die Angaben.",
  unknown: "Es ist ein Fehler aufgetreten. Bitte versuchen Sie es erneut.",
};

export class AppError extends Error {
  readonly code: AppErrorCode;
  /** Optional German message overriding the default for the code. */
  readonly userMessage: string | null;

  constructor(
    code: AppErrorCode,
    options: { userMessage?: string; cause?: unknown } = {},
  ) {
    super(options.userMessage ?? USER_ERROR_MESSAGES[code], { cause: options.cause });
    this.name = "AppError";
    this.code = code;
    this.userMessage = options.userMessage ?? null;
  }
}

/** Best-effort detection of connectivity problems. */
export function isNetworkError(error: unknown): boolean {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
  if (error instanceof TypeError) return true; // fetch() network failure
  const message =
    error && typeof error === "object" && "message" in error
      ? String((error as { message: unknown }).message)
      : "";
  return /failed to fetch|networkerror|network request failed|load failed|fetch failed/i.test(
    message,
  );
}

/** Converts anything thrown into a German message for the UI. */
export function toUserMessage(error: unknown, fallback?: string): string {
  if (error instanceof AppError) {
    return error.userMessage ?? USER_ERROR_MESSAGES[error.code];
  }
  if (isNetworkError(error)) return USER_ERROR_MESSAGES.network;
  return fallback ?? USER_ERROR_MESSAGES.unknown;
}
