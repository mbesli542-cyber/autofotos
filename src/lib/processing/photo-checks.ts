/**
 * Automatic check of every new capture ("Sofort-Prüfung" in the camera).
 *
 * Right after a capture of a template shot is SAVED (upload queue), the photo
 * is processed in the background with the AutoExperten standard style – the
 * very same processing as "Fotos bearbeiten" (`processPhoto`, results saved
 * the same way). The processor's answer tells the employee while still at the
 * car whether the shot is usable: OK (result saved) or "bitte neu aufnehmen"
 * with the processor's German reason (quality gate, other side of the car, …).
 *
 * - Only when real processing is available: a connected processor with the
 *   complete showroom (GET /api/processing-status), in demo mode with the
 *   access code stored on this device, and online. Otherwise nothing happens
 *   and the camera works as before (`availability` says why).
 * - A saved capture is queued right away ("waiting"), also while checks are
 *   off: it is checked as soon as they are on again (connection back, access
 *   code entered, processor answering again), and the photo it replaced no
 *   longer counts for the shot from that moment on.
 * - At most CHECK_CONCURRENCY checks at a time, and at most MAX_PARALLEL_JOBS
 *   processings of this tab together with "Fotos bearbeiten"; the
 *   processor's busy back-off (processing-client.ts) applies as for every job.
 * - One processing per photo and style at a time: "Fotos bearbeiten" joins a
 *   check that is still running instead of sending the photo again
 *   (`process`), and photos already processed or rejected in this visit are
 *   not sent again (`knownOutcome`) – unless the employee asks for it
 *   ("Erneut prüfen", "Bereits geprüfte Fotos erneut bearbeiten").
 * - A check belongs to one photo id. A retake is a new photo: the check of
 *   the photo it replaced is dropped (aborted when still running) and never
 *   shown for the shot.
 * - A check that could not run because the processor is gone (connection,
 *   5xx, not connected) waits again instead of reporting an error; the
 *   camera then shows that the check is off.
 * - Rejections and failed checks are kept on this device (localStorage,
 *   best effort) so the red state – and "Prüfung nicht möglich" – survives a
 *   reload, and a failed photo is not sent again on every page load. They
 *   are only shown: a stored outcome never stops "Fotos bearbeiten" from
 *   sending the photo again (the quality gate may have changed meanwhile);
 *   results are saved with the photo anyway. A refused access code is not
 *   kept (it is about the code, not the photo).
 * - Jobs survive a reload: every submitted job is kept on this device
 *   (job-journal.ts) until its outcome is known. After a reload or a
 *   discarded tab its photo is checked again – by polling that job, not by
 *   sending the photo a second time; a job the processor no longer knows
 *   (restarted) is submitted once more (processPhoto). A job whose answer
 *   could not be fetched (connection, processor asleep, the photos could not
 *   be read) stays known and is polled again when the photo is checked
 *   again. Only the jobs of the signed-in user are resumed; signing out
 *   forgets them.
 * - Opening the camera, "Fotos überprüfen" or the vehicle page checks the
 *   template photos without a result, a check or a stored rejection
 *   (`checkOnOpen`). A check that ended with an error is NOT sent again on
 *   every visit (a deterministic failure would cost the dealer's PC a job
 *   per page view): it is repeated when the checks come on again, and
 *   "Fotos bearbeiten" processes it – unless its job is still kept on the
 *   device, which is only polled.
 * - While the checks are off because the processor is not reachable (or the
 *   device is offline), its status is asked again every
 *   AVAILABILITY_POLL_MS – only while a page that needs the checks is shown
 *   (`watch`) and the tab is visible – so photos taken while the dealer's PC
 *   slept are checked once it is back.
 * - Placeholder drawings of the demo sample vehicles are never sent.
 */
import { getAppServices } from "@/lib/app-services";
import type { AuthState } from "@/lib/auth/auth-store";
import {
  isProcessingPresetId,
  type ProcessingPresetId,
  type ProcessingWarning,
  type VehiclePhoto,
  type VehiclePhotoWithUrls,
} from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { isOlderCapture } from "@/lib/photos/capture-order";
import type { ShotTemplate } from "@/lib/shots/shot-template";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";
import { readStoredAccessCode, storeAccessCode, subscribeAccessCode } from "./access-code-storage";
import {
  ProcessingJobFailedError,
  processPhoto,
  type PhotoProcessingResult,
  type PhotoStep,
} from "./photo-processing";
import { clearJobJournal, findJob, forgetJob, readJobJournal, recordJob } from "./job-journal";
import {
  AccessCodeRequiredError,
  ProcessingNotConnectedError,
  fetchProcessingStatus,
  jobMayStillRun,
} from "./processing-client";
import { hasRetakeWarning, warningsToCheck, getWarningKind } from "./processing-warnings";
import { needsRetake, retakeFallbackMessage } from "./quality-gate";
import { isPlaceholderPhoto } from "./run-blockers";
import { ACCESS_CODE_INVALID_CODE } from "./shot-check";
import { getShotTreatment } from "./shot-treatment";
import { canStartProcessing, type ProcessingStatus } from "./types";

/** The style of the automatic check (the dealer's standard showroom). */
export const CHECK_PRESET: ProcessingPresetId = "autoexperten_standard";

/** Checks running at the same time (the processor works through its own queue). */
export const CHECK_CONCURRENCY = 2;

/** Processings of this tab at the same time – checks and "Fotos bearbeiten" together. */
export const MAX_PARALLEL_JOBS = 3;

/** The processor status is asked again after this long. */
const STATUS_MAX_AGE_MS = 30_000;

/** While the checks are off (processor not reachable, offline), its status is asked again this often. */
export const AVAILABILITY_POLL_MS = 60_000;

const STORAGE_KEY = "ae-photo.photo-checks.v1";
/** Rejections kept on the device (oldest dropped first). */
const MAX_STORED = 300;

const GENERIC_FAILURE = "Bearbeitung fehlgeschlagen.";
const PHOTOS_UNREADABLE_MESSAGE = "Die Fotos konnten gerade nicht geladen werden.";
const ACCESS_CODE_INVALID_MESSAGE = "Der Zugangscode ist falsch – bitte neu eingeben.";

/**
 * - waiting:  saved, the check starts as soon as a slot is free
 * - checking: being processed
 * - ok:       result saved (it may carry hints to check)
 * - retake:   the processor rejected the photo, or its result says the photo
 *             itself is the problem → "bitte neu aufnehmen"
 * - error:    could not be checked (connection, processor busy for too long, …);
 *             "Fotos bearbeiten" processes it later
 */
export type PhotoCheckStatus = "waiting" | "checking" | "ok" | "retake" | "error";

export interface PhotoCheck {
  photoId: string;
  vehicleId: string;
  shotKey: string;
  shotOrder: number;
  title: string;
  /** Capture time (ISO) – decides which photo of a shot is the newer one. */
  takenAt: string;
  preset: ProcessingPresetId;
  status: PhotoCheckStatus;
  /** German: the reason to retake, or why the check was not possible. */
  message: string | null;
  /** The processor's reason of a rejected job (quality-gate code, …). */
  errorCode: string | null;
  /** The processor's hints on a saved result. */
  warnings: readonly ProcessingWarning[];
  /** A rejection read back from this device's storage: shown, never a reason to skip processing. */
  restored?: boolean;
}

/** The newest saved capture of a shot (`vehicleId/shotKey`) known in this tab. */
export interface SavedCapture {
  photoId: string;
  takenAt: string;
}

/**
 * Whether captures are checked automatically:
 * - unknown:      not asked yet
 * - on
 * - no_processor: no processor connected (or the showroom is not ready)
 * - access_code:  demo mode – the access code is not stored on this device
 * - offline:      no connection
 */
export type AutoCheckAvailability = "unknown" | "on" | "no_processor" | "access_code" | "offline";

export interface PhotoChecksSnapshot {
  availability: AutoCheckAvailability;
  /** By photo id. */
  checks: ReadonlyMap<string, PhotoCheck>;
  /**
   * The newest capture saved in this tab per shot (`vehicleId/shotKey`): a
   * photo older than it no longer counts for the shot, even while the photo
   * list on screen still shows it.
   */
  captures: ReadonlyMap<string, SavedCapture>;
}

/** What is already known about processing a photo with a style – it is not sent again. */
export type KnownOutcome =
  | { status: "complete"; warnings: ProcessingWarning[] }
  | { status: "failed"; error: string; errorCode: string | null };

export interface KnownOutcomeOptions {
  /** A rejection is no reason to skip ("Erneut prüfen", "Erneut versuchen"). */
  ignoreFailures?: boolean;
}

export interface ProcessOptions {
  accessCode: string | null;
  signal?: AbortSignal;
  onStep?: (step: PhotoStep, progress?: number) => void;
}

/** One processing of one photo with one style, shared by everyone waiting for it. */
interface SharedJob {
  promise: Promise<PhotoProcessingResult>;
  controller: AbortController;
  users: number;
  steps: Set<(step: PhotoStep, progress?: number) => void>;
}

/** A rejection or a failed check kept on this device (`status` missing: a rejection kept before failures were). */
type StoredOutcome = Pick<
  PhotoCheck,
  "photoId" | "vehicleId" | "shotKey" | "shotOrder" | "title" | "takenAt" | "preset" | "message" | "errorCode"
> & { status?: "retake" | "error" };

/** Whether the outcome of a check is kept on this device (pure): a rejection by its code, or a failed check. */
export function isKeptOutcome(check: Pick<PhotoCheck, "status" | "errorCode">): boolean {
  if (check.status === "retake") return check.errorCode !== null;
  return check.status === "error" && check.errorCode !== ACCESS_CODE_INVALID_CODE;
}

const EMPTY: PhotoChecksSnapshot = { availability: "unknown", checks: new Map(), captures: new Map() };

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** The first hint that asks for a retake (its message), else null. */
function retakeWarningMessage(warnings: readonly ProcessingWarning[]): string | null {
  return warningsToCheck(warnings).find((warning) => getWarningKind(warning.code) === "retake")?.message ?? null;
}

/** The signed-in user's id, null when signed out, undefined while still unknown. */
function sessionUser(auth: AuthState): string | null | undefined {
  if (auth.status === "loading") return undefined;
  return auth.status === "authenticated" ? auth.user.id : null;
}

/** The signed-in user's id right now (null: signed out or still unknown). */
function signedInUser(): string | null {
  return sessionUser(getAppServices().authStore.getSnapshot()) ?? null;
}

/** Key of a shot of a vehicle (`captures`). */
export function shotSlotKey(check: Pick<PhotoCheck, "vehicleId" | "shotKey">): string {
  return `${check.vehicleId}/${check.shotKey}`;
}

/**
 * Whether the processor status is asked again every AVAILABILITY_POLL_MS
 * (pure): while the checks are off because it is not reachable or the
 * device is offline – only while a page that needs the checks is shown
 * (`watched`) and the tab is visible. A missing access code needs the
 * employee, not a poll.
 */
export function shouldPollAvailability(availability: AutoCheckAvailability, watched: boolean, visible: boolean): boolean {
  return watched && visible && (availability === "no_processor" || availability === "offline");
}

/**
 * What opening a page that shows the checks (camera, "Fotos überprüfen",
 * vehicle page) does with one of the vehicle's photos (pure):
 * - "check":  a template photo without a result, a check or a stored
 *             rejection – it is checked
 * - "resume": its check could not run, but its job is still kept on this
 *             device (`jobKept`, e.g. the photos could not be read right
 *             after a reload) – that job is polled, the photo is not sent again
 * - null:     nothing to do – also for a check that ended with an error and
 *             has no job any more: it is not sent again on every visit (it is
 *             repeated when the checks come on again, or by "Fotos bearbeiten")
 * Placeholder drawings of the demo samples are never sent.
 */
export function checkOnOpen(
  photo: Pick<VehiclePhoto, "shotKey" | "processedStoragePath" | "originalStoragePath">,
  template: ShotTemplate,
  check: Pick<PhotoCheck, "status"> | undefined,
  jobKept: boolean,
): "check" | "resume" | null {
  if (getShotTreatment(template, photo.shotKey) === null || photo.processedStoragePath) return null;
  if (isPlaceholderPhoto(photo)) return null;
  if (!check) return "check";
  return check.status === "error" && jobKept ? "resume" : null;
}

function documentVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

/**
 * The processor is gone or not reachable (no connection, 5xx, not connected):
 * nothing about the photo itself – it is checked again later.
 */
function isUnavailableError(error: unknown): boolean {
  return !(error instanceof ProcessingJobFailedError) && !(error instanceof AccessCodeRequiredError);
}

/**
 * What is known about processing `photo` with `preset` (pure): its saved
 * result (on the photo, or saved by its check in this visit), or the
 * processor's rejection of exactly this photo in this visit
 * (`check`: the photo's check; a rejection restored from the device's
 * storage does not count – the quality gate may have changed since).
 * null → it has to be processed.
 */
export function knownOutcomeOf(
  photo: Pick<VehiclePhotoWithUrls, "id" | "processedStoragePath" | "processedPreset" | "processingWarnings">,
  preset: ProcessingPresetId,
  check: PhotoCheck | undefined,
  options: KnownOutcomeOptions = {},
): KnownOutcome | null {
  if (photo.processedStoragePath && photo.processedPreset === preset) {
    return { status: "complete", warnings: [...photo.processingWarnings] };
  }
  // Its result was saved in this visit (the photo list on screen may not show it yet).
  const saved = check?.status === "ok" || (check?.status === "retake" && check.errorCode === null);
  if (check && saved && check.photoId === photo.id && check.preset === preset) {
    return { status: "complete", warnings: [...check.warnings] };
  }
  if (
    !options.ignoreFailures &&
    check?.photoId === photo.id &&
    check.status === "retake" &&
    !check.restored &&
    check.preset === preset &&
    check.errorCode !== null
  ) {
    return { status: "failed", error: check.message ?? GENERIC_FAILURE, errorCode: check.errorCode };
  }
  return null;
}

export class PhotoChecks {
  private snapshot: PhotoChecksSnapshot = EMPTY;
  private readonly listeners = new Set<() => void>();
  private readonly settledListeners = new Set<(check: PhotoCheck) => void>();
  private readonly jobs = new Map<string, SharedJob>();
  /** Aborts the check of a photo (by photo id) – a retake replaced it, or the session ended. */
  private readonly checkControllers = new Map<string, AbortController>();
  private readonly queue: string[] = [];
  private active = 0;
  /** Processings running (checks and runs together, MAX_PARALLEL_JOBS). */
  private runningJobs = 0;
  private readonly jobWaiters: (() => void)[] = [];
  private started = false;
  private status: ProcessingStatus | null = null;
  private statusAt = 0;
  private statusRequest: Promise<ProcessingStatus | null> | null = null;
  /** Pages shown right now that need the checks (`watch`). */
  private watchers = 0;
  /**
   * The processor refused the stored access code in this tab: the checks stay
   * off until a code is entered again (or the user changes) – also while a
   * status asked before (cached) does not ask for one, so a refused code never
   * repeats checks in a loop.
   */
  private codeRefused = false;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  /* ---------- useSyncExternalStore API ---------- */

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): PhotoChecksSnapshot => this.snapshot;

  getServerSnapshot = (): PhotoChecksSnapshot => EMPTY;

  /** Called when a check (or a run's processing) of a photo ended with ok, retake or error. */
  onSettled(listener: (check: PhotoCheck) => void): () => void {
    this.settledListeners.add(listener);
    return () => this.settledListeners.delete(listener);
  }

  /* ---------- lifecycle ---------- */

  /**
   * Starts listening for saved captures (once per tab; browser only):
   * restores the rejections stored on this device, resumes the jobs
   * submitted before a reload and follows the connection, the access code,
   * the tab's visibility and the signed-in user.
   */
  start(): void {
    if (this.started || typeof window === "undefined") return;
    this.started = true;
    const { uploads, authStore } = getAppServices();
    this.restore();
    let user = sessionUser(authStore.getSnapshot());
    // Only the signed-in user's jobs are resumed – once the user is known.
    if (typeof user === "string") this.resumeJobs(user);
    uploads.onUploaded((item) => void this.captureSaved(item));
    window.addEventListener("online", () => void this.refreshAvailability(true));
    window.addEventListener("offline", () => this.setAvailability("offline"));
    document.addEventListener("visibilitychange", () => {
      // Back on screen while the checks are off: ask right away, then every minute.
      if (documentVisible() && shouldPollAvailability(this.snapshot.availability, this.watchers > 0, true)) {
        void this.refreshAvailability(true);
      }
      this.updatePolling();
    });
    subscribeAccessCode(() => {
      // A new code: it is tried (the refusal was of the code before).
      if (readStoredAccessCode()) this.codeRefused = false;
      void this.refreshAvailability();
    });
    authStore.subscribe(() => {
      const next = sessionUser(authStore.getSnapshot());
      if (next === undefined || next === user) return;
      const changed = user !== undefined;
      user = next;
      if (changed) this.stopAll();
      if (typeof next === "string") this.resumeJobs(next);
    });
  }

  /**
   * Asks the processor status (cached for STATUS_MAX_AGE_MS unless `force`)
   * and decides whether captures are checked.
   */
  async refreshAvailability(force = false): Promise<AutoCheckAvailability> {
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.setAvailability("offline");
      return "offline";
    }
    const status = await this.processingStatus(force);
    let availability: AutoCheckAvailability = "on";
    if (!canStartProcessing(status)) availability = "no_processor";
    else if ((status?.accessCodeRequired || this.codeRefused) && !readStoredAccessCode()) availability = "access_code";
    this.setAvailability(availability);
    if (availability === "on") this.pump();
    return availability;
  }

  /**
   * Checks the vehicle's current template photos that have no result, no
   * check and no stored rejection yet (`checkOnOpen`) – when the camera,
   * "Fotos überprüfen" or the vehicle page is shown with the checks on (also
   * right after they came on), and after the access code was entered in the
   * camera. A check that ended with an error is not sent again here (only
   * its job polled while it is still kept on the device).
   */
  async checkVehicle(vehicleId: string): Promise<void> {
    if ((await this.refreshAvailability()) !== "on") return;
    let photos;
    try {
      photos = await getAppServices().backend.data.listPhotos(vehicleId);
    } catch {
      return;
    }
    const { template } = getAppServices();
    const user = signedInUser();
    for (const photo of photos) {
      const check = this.snapshot.checks.get(photo.id);
      const job = check ? findJob(photo.id, check.preset) : null;
      const action = checkOnOpen(photo, template, check, job !== null && job.userId === user);
      if (action === "check") this.enqueue({ ...photo, preset: CHECK_PRESET });
      else if (action === "resume") this.requeue(photo.id);
    }
    this.pump();
  }

  /**
   * A page that needs the checks is shown (camera, "Fotos überprüfen",
   * vehicle page): while it is, the processor status is asked again every
   * AVAILABILITY_POLL_MS as long as the checks are off because it is not
   * reachable. Returns the function to call when the page is hidden.
   */
  watch(): () => void {
    this.watchers += 1;
    this.updatePolling();
    let watching = true;
    return () => {
      if (!watching) return;
      watching = false;
      this.watchers -= 1;
      this.updatePolling();
    };
  }

  /** Ends every check (signed out / another user). Stored outcomes stay with their photo ids. */
  stopAll(): void {
    // The jobs of the previous user are not resumed after a reload.
    clearJobJournal();
    this.codeRefused = false;
    this.queue.length = 0;
    for (const controller of this.checkControllers.values()) controller.abort();
    this.checkControllers.clear();
    const checks = new Map(
      [...this.snapshot.checks].filter(([, check]) => check.status !== "waiting" && check.status !== "checking"),
    );
    this.set({ ...this.snapshot, checks });
  }

  /* ---------- shared with the processing runs ---------- */

  /** What is known about processing `photo` with `preset` (see knownOutcomeOf) – it is not sent again. */
  knownOutcome(
    photo: VehiclePhotoWithUrls,
    preset: ProcessingPresetId,
    options: KnownOutcomeOptions = {},
  ): KnownOutcome | null {
    return knownOutcomeOf(photo, preset, this.snapshot.checks.get(photo.id), options);
  }

  /**
   * Processes `photo` (processPhoto: result saved like every processing) –
   * or joins its processing with the same style that is already running.
   * Aborting `signal` stops waiting; the processing itself stops once nobody
   * waits for it any more. Records the outcome as the photo's check.
   */
  process(photo: VehiclePhotoWithUrls, preset: ProcessingPresetId, options: ProcessOptions): Promise<PhotoProcessingResult> {
    const key = `${photo.id}:${preset}`;
    let job = this.jobs.get(key);
    if (!job) job = this.createJob(key, photo, preset, options.accessCode);
    return this.join(job, options);
  }

  /* ---------- internals ---------- */

  /**
   * The jobs `user` submitted before a reload (or a discarded tab) whose
   * outcome is not known yet: their photos are checked again – by polling
   * the job (createJob), not by sending the photo again. Jobs of another
   * user (a shared device, a session that expired) are forgotten.
   */
  private resumeJobs(user: string): void {
    const { template } = getAppServices();
    const entries = readJobJournal().filter((entry) => {
      if (entry.userId === user && getShotTreatment(template, entry.shotKey) !== null) return true;
      forgetJob(entry.photoId, entry.preset);
      return false;
    });
    for (const entry of entries) {
      this.enqueue({
        id: entry.photoId,
        vehicleId: entry.vehicleId,
        shotKey: entry.shotKey,
        shotOrder: entry.shotOrder,
        title: entry.title,
        takenAt: entry.takenAt,
        preset: entry.preset,
      });
    }
    if (entries.length > 0) void this.refreshAvailability(); // polled as soon as the checks are on
  }

  /** Starts or stops asking the processor status every AVAILABILITY_POLL_MS (shouldPollAvailability). */
  private updatePolling(): void {
    const poll = shouldPollAvailability(this.snapshot.availability, this.watchers > 0, documentVisible());
    if (poll && this.pollTimer === null) {
      this.pollTimer = setInterval(() => void this.refreshAvailability(true), AVAILABILITY_POLL_MS);
    } else if (!poll && this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private async processingStatus(force: boolean): Promise<ProcessingStatus | null> {
    if (!force && this.status && Date.now() - this.statusAt < STATUS_MAX_AGE_MS) return this.status;
    this.statusRequest ??= fetchProcessingStatus()
      .then(
        (status) => status,
        () => null,
      )
      .then((status) => {
        this.status = status;
        this.statusAt = status ? Date.now() : 0;
        this.statusRequest = null;
        return status;
      });
    return this.statusRequest;
  }

  /**
   * A capture was saved: from now on it is the shot's photo (the photo it
   * replaced and that photo's check no longer count), and it is checked –
   * right away when checks are on, else as soon as they are on again.
   * Synchronous up to the status request, so no screen ever shows the
   * replaced photo's result in between.
   */
  private async captureSaved(item: UploadItem): Promise<void> {
    const { template } = getAppServices();
    if (getShotTreatment(template, item.shotKey) === null) return;
    const slot = shotSlotKey(item);
    const newest = this.snapshot.captures.get(slot);
    // An older capture that arrives late is stored archived: it is not the shot's photo.
    if (newest && newest.photoId !== item.id && isOlderCapture(item.takenAt, newest.takenAt)) return;
    const captures = new Map(this.snapshot.captures);
    captures.set(slot, { photoId: item.id, takenAt: item.takenAt });
    this.set({ ...this.snapshot, captures });
    // A newer capture of the shot replaces the photos before it: their checks are dropped.
    for (const check of this.snapshot.checks.values()) {
      if (shotSlotKey(check) === slot && check.photoId !== item.id && isOlderCapture(check.takenAt, item.takenAt)) {
        this.drop(check.photoId);
      }
    }
    this.enqueue({
      id: item.id,
      vehicleId: item.vehicleId,
      shotKey: item.shotKey,
      shotOrder: item.shotOrder,
      title: item.title,
      takenAt: item.takenAt,
      preset: CHECK_PRESET,
    });
    await this.refreshAvailability(); // starts the check when checks are on
  }

  private enqueue(photo: {
    id: string;
    vehicleId: string;
    shotKey: string;
    shotOrder: number;
    title: string;
    takenAt: string;
    preset: ProcessingPresetId;
  }): void {
    if (this.queue.includes(photo.id) || this.checkControllers.has(photo.id)) return;
    // Being processed already (by "Fotos bearbeiten"): its outcome is the check.
    if (this.jobs.has(`${photo.id}:${photo.preset}`)) return;
    this.queue.push(photo.id);
    this.checkControllers.set(photo.id, new AbortController());
    this.putCheck({
      photoId: photo.id,
      vehicleId: photo.vehicleId,
      shotKey: photo.shotKey,
      shotOrder: photo.shotOrder,
      title: photo.title,
      takenAt: photo.takenAt,
      preset: photo.preset,
      status: "waiting",
      message: null,
      errorCode: null,
      warnings: [],
    });
  }

  /** A check of `photoId` that could not run waits again (checked when checks are on). */
  private requeue(photoId: string): void {
    const check = this.snapshot.checks.get(photoId);
    if (!check || this.jobs.has(`${photoId}:${check.preset}`)) return;
    this.checkControllers.get(photoId)?.abort();
    this.checkControllers.set(photoId, new AbortController());
    if (!this.queue.includes(photoId)) this.queue.push(photoId);
    this.putCheck({ ...check, status: "waiting", message: null, errorCode: null, warnings: [], restored: false });
  }

  /** Starts waiting checks while slots are free and checks are on. */
  private pump(): void {
    while (this.active < CHECK_CONCURRENCY && this.snapshot.availability === "on" && this.queue.length > 0) {
      const photoId = this.queue.shift() as string;
      this.active += 1;
      void this.runCheck(photoId).finally(() => {
        this.active -= 1;
        this.pump();
      });
    }
  }

  private async runCheck(photoId: string): Promise<void> {
    const check = this.snapshot.checks.get(photoId);
    const controller = this.checkControllers.get(photoId);
    try {
      // Processed meanwhile by "Fotos bearbeiten" (its outcome is the check), or dropped.
      if (!check || !controller || controller.signal.aborted || check.status !== "waiting") return;
      const photo = await this.currentPhoto(check);
      if (controller.signal.aborted) return;
      if (photo === "replaced") {
        // Replaced by a newer photo (or deleted) meanwhile – nothing to check.
        this.drop(photoId);
        return;
      }
      if (photo === "unreadable") {
        await this.photosUnreadable(photoId, controller);
        return;
      }
      if (this.snapshot.checks.get(photoId)?.status !== "waiting") return;
      try {
        await this.process(photo, check.preset, {
          accessCode: readStoredAccessCode(),
          signal: controller.signal,
        });
        await this.syncVehicleStatus(photo.vehicleId);
      } catch {
        // Recorded as the photo's check (createJob).
      }
    } finally {
      if (controller && this.checkControllers.get(photoId) === controller) this.checkControllers.delete(photoId);
    }
  }

  /**
   * The check's photo if it is still the current photo of its shot (with its
   * URLs); "replaced" when a newer photo replaced it (or it was deleted);
   * "unreadable" when the photos could not be read right now (e.g. a weak
   * connection right after a reload) – that says nothing about the photo.
   */
  private async currentPhoto(check: PhotoCheck): Promise<VehiclePhotoWithUrls | "replaced" | "unreadable"> {
    const { backend } = getAppServices();
    let current: VehiclePhoto | undefined;
    try {
      current = (await backend.data.listPhotos(check.vehicleId)).find((photo) => photo.shotKey === check.shotKey);
    } catch {
      return "unreadable";
    }
    if (!current || current.id !== check.photoId) return "replaced";
    try {
      const [resolved] = await backend.data.resolvePhotoUrls([current]);
      return resolved ?? "unreadable";
    } catch {
      return "unreadable";
    }
  }

  /**
   * The check's photo could not be read (nothing was sent): its job, if one
   * was submitted before a reload, stays kept on the device – it is polled
   * the next time instead of sending the photo again. While the processor is
   * not reachable either the check waits. Otherwise it ends as "not possible"
   * for now and the next page open polls its job (checkOnOpen "resume") – or,
   * without a job, checks the photo as one without a check.
   */
  private async photosUnreadable(photoId: string, controller: AbortController): Promise<void> {
    const availability = await this.refreshAvailability(true);
    const check = this.snapshot.checks.get(photoId);
    if (controller.signal.aborted || check?.status !== "waiting") return;
    if (availability !== "on") {
      this.requeue(photoId);
      return;
    }
    const job = findJob(photoId, check.preset);
    if (job === null || job.userId !== signedInUser()) {
      this.drop(photoId);
      return;
    }
    this.settle(photoId, { status: "error", message: PHOTOS_UNREADABLE_MESSAGE, errorCode: null, warnings: [] });
  }

  /** All required shots processed and the capture completed: the vehicle counts as processed. */
  private async syncVehicleStatus(vehicleId: string): Promise<void> {
    try {
      const { backend, template } = getAppServices();
      const vehicle = await backend.data.getVehicle(vehicleId);
      if (vehicle?.status === "complete") await markProcessedIfDone(backend.data, template, vehicleId);
    } catch {
      // Synced again by "Fotos bearbeiten".
    }
  }

  private createJob(
    key: string,
    photo: VehiclePhotoWithUrls,
    preset: ProcessingPresetId,
    accessCode: string | null,
  ): SharedJob {
    const controller = new AbortController();
    const steps = new Set<(step: PhotoStep, progress?: number) => void>();
    const { backend } = getAppServices();
    const base = this.snapshot.checks.get(photo.id);
    const takenAt = base?.takenAt ?? photo.takenAt;
    // Jobs are kept per user: only this user's job of the photo is polled.
    const user = signedInUser();
    const kept = findJob(photo.id, preset);
    this.putCheck({
      photoId: photo.id,
      vehicleId: photo.vehicleId,
      shotKey: photo.shotKey,
      shotOrder: photo.shotOrder,
      title: photo.title,
      takenAt,
      preset,
      status: "checking",
      message: null,
      errorCode: null,
      warnings: [],
    });
    const promise = this.acquireJobSlot(controller.signal).then(() =>
      processPhoto(photo, preset, {
        mode: backend.mode,
        data: backend.data,
        accessCode,
        signal: controller.signal,
        onStep: (step, progress) => {
          for (const listener of steps) listener(step, progress);
        },
        // Submitted before a reload: its answer is fetched instead of sending the photo again.
        resumeJobId: kept !== null && kept.userId === user ? kept.jobId : null,
        onSubmitted: (jobId) => {
          if (user === null) return; // nobody to resume it for
          recordJob({
            userId: user,
            vehicleId: photo.vehicleId,
            photoId: photo.id,
            jobId,
            preset,
            startedAt: new Date().toISOString(),
            shotKey: photo.shotKey,
            shotOrder: photo.shotOrder,
            title: photo.title,
            takenAt,
          });
        },
      }).finally(() => this.releaseJobSlot()),
    );
    const job: SharedJob = { promise, controller, users: 0, steps };
    this.jobs.set(key, job);
    promise.then(
      ({ warnings }) => {
        this.jobs.delete(key);
        forgetJob(photo.id, preset);
        const retake = hasRetakeWarning(warnings);
        this.settle(photo.id, {
          status: retake ? "retake" : "ok",
          message: retake ? retakeWarningMessage(warnings) : null,
          errorCode: null,
          warnings,
        });
      },
      (error: unknown) => {
        this.jobs.delete(key);
        // Its answer may still come (connection or processor gone for a while): polled again later.
        if (controller.signal.aborted || isAbort(error) || !jobMayStillRun(error)) forgetJob(photo.id, preset);
        if (controller.signal.aborted || isAbort(error)) {
          // Nobody waits for it any more (retake, reset, signed out).
          this.drop(photo.id);
          return;
        }
        if (error instanceof AccessCodeRequiredError) {
          // Off until a code is entered again (the status is asked anew then).
          this.codeRefused = true;
          this.status = null;
          storeAccessCode(null);
          this.setAvailability("access_code");
          this.settle(photo.id, {
            status: "error",
            message: ACCESS_CODE_INVALID_MESSAGE,
            errorCode: ACCESS_CODE_INVALID_CODE,
            warnings: [],
          });
          return;
        }
        if (isUnavailableError(error)) {
          // The processor is gone or not reachable: ask again – while it is off, the photo
          // waits (the camera shows that the check is off) instead of an error per capture.
          if (error instanceof ProcessingNotConnectedError) this.status = null;
          void this.refreshAvailability(true).then((availability) => {
            if (this.snapshot.checks.get(photo.id)?.status !== "checking" || this.jobs.has(key)) return;
            if (availability !== "on") {
              this.requeue(photo.id);
              return;
            }
            this.settle(photo.id, {
              status: "error",
              message: toUserMessage(error, GENERIC_FAILURE),
              errorCode: null,
              warnings: [],
            });
          });
          return;
        }
        if (error instanceof ProcessingJobFailedError && needsRetake(error.errorCode)) {
          this.settle(photo.id, {
            status: "retake",
            message: toUserMessage(error, retakeFallbackMessage(error.errorCode) ?? GENERIC_FAILURE),
            errorCode: error.errorCode,
            warnings: [],
          });
          return;
        }
        this.settle(photo.id, {
          status: "error",
          message: toUserMessage(error, GENERIC_FAILURE),
          errorCode: error instanceof ProcessingJobFailedError ? error.errorCode : null,
          warnings: [],
        });
      },
    );
    return job;
  }

  /** Waits for a free processing slot (MAX_PARALLEL_JOBS); aborting `signal` stops waiting. */
  private acquireJobSlot(signal: AbortSignal): Promise<void> {
    if (signal.aborted) return Promise.reject(abortError());
    if (this.runningJobs < MAX_PARALLEL_JOBS) {
      this.runningJobs += 1;
      return Promise.resolve();
    }
    return new Promise<void>((resolve, reject) => {
      const start = () => {
        signal.removeEventListener("abort", onAbort);
        this.runningJobs += 1;
        resolve();
      };
      const onAbort = () => {
        const index = this.jobWaiters.indexOf(start);
        if (index >= 0) this.jobWaiters.splice(index, 1);
        reject(abortError());
      };
      signal.addEventListener("abort", onAbort, { once: true });
      this.jobWaiters.push(start);
    });
  }

  private releaseJobSlot(): void {
    this.runningJobs -= 1;
    this.jobWaiters.shift()?.();
  }

  /** Waits for `job` on behalf of one caller (its own abort signal and step listener). */
  private join(job: SharedJob, options: ProcessOptions): Promise<PhotoProcessingResult> {
    const { signal, onStep } = options;
    if (signal?.aborted) return Promise.reject(abortError());
    job.users += 1;
    if (onStep) job.steps.add(onStep);
    return new Promise<PhotoProcessingResult>((resolve, reject) => {
      const leave = () => {
        job.users -= 1;
        if (onStep) job.steps.delete(onStep);
        signal?.removeEventListener("abort", onAbort);
      };
      const onAbort = () => {
        leave();
        if (job.users === 0) job.controller.abort();
        reject(abortError());
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      job.promise.then(
        (result) => {
          if (signal?.aborted) return;
          leave();
          resolve(result);
        },
        (error: unknown) => {
          if (signal?.aborted) return;
          leave();
          reject(error);
        },
      );
    });
  }

  private settle(photoId: string, patch: Pick<PhotoCheck, "status" | "message" | "errorCode" | "warnings">): void {
    const check = this.snapshot.checks.get(photoId);
    if (!check) return;
    const settled = { ...check, ...patch };
    this.putCheck(settled);
    this.persist();
    for (const listener of this.settledListeners) listener(settled);
  }

  /** Forgets the check of a photo (aborts it while it waits or runs) and its job. */
  private drop(photoId: string): void {
    this.checkControllers.get(photoId)?.abort();
    this.checkControllers.delete(photoId);
    forgetJob(photoId);
    const index = this.queue.indexOf(photoId);
    if (index >= 0) this.queue.splice(index, 1);
    if (!this.snapshot.checks.has(photoId)) return;
    const checks = new Map(this.snapshot.checks);
    checks.delete(photoId);
    this.set({ ...this.snapshot, checks });
    this.persist();
  }

  private putCheck(check: PhotoCheck): void {
    const checks = new Map(this.snapshot.checks);
    checks.set(check.photoId, check);
    this.set({ ...this.snapshot, checks });
  }

  private setAvailability(availability: AutoCheckAvailability): void {
    const previous = this.snapshot.availability;
    if (previous === availability) return;
    this.set({ ...this.snapshot, availability });
    this.updatePolling();
    // On again (connection back, code entered, processor answering): checks that could not run are repeated.
    if (availability === "on" && previous !== "unknown") {
      for (const check of this.snapshot.checks.values()) {
        if (check.status === "error") this.requeue(check.photoId);
      }
    }
  }

  /** Rejected photos (by the processor's code) and failed checks are kept on this device (isKeptOutcome). */
  private persist(): void {
    const rejections: StoredOutcome[] = [...this.snapshot.checks.values()]
      .filter(isKeptOutcome)
      .slice(-MAX_STORED)
      .map(({ photoId, vehicleId, shotKey, shotOrder, title, takenAt, preset, status, message, errorCode }) => ({
        photoId,
        vehicleId,
        shotKey,
        shotOrder,
        title,
        takenAt,
        preset,
        status: status === "error" ? "error" : "retake",
        message,
        errorCode,
      }));
    try {
      if (rejections.length > 0) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rejections));
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Storage blocked or full: the states stay for this visit.
    }
  }

  private restore(): void {
    let stored: unknown;
    try {
      stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]");
    } catch {
      return;
    }
    if (!Array.isArray(stored)) return;
    const checks = new Map(this.snapshot.checks);
    for (const entry of stored as Partial<StoredOutcome>[]) {
      const failed = entry?.status === "error";
      if (
        typeof entry?.photoId !== "string" ||
        typeof entry.vehicleId !== "string" ||
        typeof entry.shotKey !== "string" ||
        !(typeof entry.errorCode === "string" || (failed && entry.errorCode === null)) ||
        checks.has(entry.photoId)
      ) {
        continue;
      }
      const errorCode = entry.errorCode ?? null;
      checks.set(entry.photoId, {
        photoId: entry.photoId,
        vehicleId: entry.vehicleId,
        shotKey: entry.shotKey,
        shotOrder: typeof entry.shotOrder === "number" ? entry.shotOrder : 0,
        title: typeof entry.title === "string" ? entry.title : entry.shotKey,
        takenAt: typeof entry.takenAt === "string" ? entry.takenAt : "",
        preset: isProcessingPresetId(entry.preset) ? entry.preset : CHECK_PRESET,
        status: failed ? "error" : "retake",
        message:
          typeof entry.message === "string"
            ? entry.message
            : failed
              ? GENERIC_FAILURE
              : retakeFallbackMessage(errorCode ?? ""),
        errorCode,
        warnings: [],
        restored: true,
      });
    }
    this.set({ ...this.snapshot, checks });
  }

  private set(snapshot: PhotoChecksSnapshot): void {
    this.snapshot = snapshot;
    for (const listener of this.listeners) listener();
  }
}

/** The checks of this browser tab (module-level, shared by the camera and every page). */
export const photoChecks = new PhotoChecks();
