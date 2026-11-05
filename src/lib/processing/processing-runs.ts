/**
 * Processing runs ("Fotos verarbeiten"), kept per vehicle outside the
 * screen – like the upload queue. Next.js keeps only a few routes mounted,
 * so the processing page can be re-created while its run is still going on:
 * it re-attaches to the run (read with useSyncExternalStore) and shows its
 * progress or result instead of offering to process the same photos again.
 *
 * A run processes the template shots of a vehicle (extra photos are
 * skipped), at most CONCURRENCY photos at a time. Right before submitting,
 * `start` and `retry` read the vehicle's current photos again: the list on
 * screen can be older (a retake saved a moment ago, its reload still on its
 * way) and would make the run process the photo it replaced. Each shot is
 * read again right before its photo is submitted, too: a retake saved while
 * the shot waited in the run (e.g. taken after leaving the screen) is
 * processed instead of the photo it replaced. The adapter
 * follows the data backend (Supabase contract or demo upload); only real
 * results are saved, with the processor's hints (`warnings`), failures save
 * nothing. Shots the processor rejected (e.g. quality gate) stay failed
 * while the rest of the run continues; `retry` processes shots again – the
 * new photos after "Foto neu aufnehmen" or the same photo after a temporary
 * failure.
 *
 * Photos whose result is already known – processed with the run's style
 * (e.g. by the automatic check right after the capture, photo-checks.ts) or
 * rejected by the processor in this visit – are not sent again: they show as
 * done or failed at once. A check still running is joined instead of sending
 * the photo a second time. `retry` ("Erneut versuchen", "Erneut prüfen")
 * always sends a rejected photo again, and a run started with
 * `reprocess: true` ("Bereits geprüfte Fotos erneut bearbeiten") sends every
 * photo again (e.g. after a showroom update).
 *
 * Runs belong to the signed-in user: when the session ends or another user
 * signs in (shared devices), every run is stopped – its uploads and polls
 * are aborted – and the next person starts with the style selection.
 *
 * A run survives a reload or a discarded tab (iPhone Safari): it is kept on
 * the device while it runs (stored-runs.ts) and `resume` – called when its
 * processing page is shown – continues it: finished rows stay as they were,
 * the other rows are processed again, and a photo whose job was submitted
 * before the reload is not sent again – its job is polled (job-journal.ts,
 * photo-checks.ts).
 */
import { getAppServices } from "@/lib/app-services";
import type { AuthState } from "@/lib/auth/auth-store";
import type { ProcessingPresetId, ProcessingWarning, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import { isOlderCapture } from "@/lib/photos/capture-order";
import { sortPhotosByShotOrder } from "@/lib/shots/shot-progress";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";
import { readStoredAccessCode, storeAccessCode } from "./access-code-storage";
import { ProcessingJobFailedError, type PhotoStep } from "./photo-processing";
import { photoChecks, type PhotoCheck } from "./photo-checks";
import { PRESET_UNAVAILABLE_MESSAGE, isProcessingPresetAvailable } from "./presets";
import { AccessCodeRequiredError } from "./processing-client";
import { getShotTreatment, selectPhotosForProcessing, type ShotTreatment } from "./shot-treatment";
import { forgetRun, readStoredRun, storeRun, type StoredRunItem } from "./stored-runs";

export type PhotoRunStatus = "waiting" | PhotoStep;

export interface PhotoRunState {
  photoId: string;
  shotKey: string;
  title: string;
  shotOrder: number;
  treatment: ShotTreatment;
  status: PhotoRunStatus;
  progress: number;
  /** German message (the processor's own text where available). */
  error: string | null;
  /** Processor reason of a failed job (e.g. a quality-gate code), see quality-gate.ts. */
  errorCode: string | null;
  /** The processor's hints on the saved result (German), see processing-warnings.ts. */
  warnings: readonly ProcessingWarning[];
}

export type RunPhase = "idle" | "running" | "done";

/** "cancelled": the run was reset or the signed-in user changed meanwhile. */
export type RunOutcome =
  | "done"
  | "access_denied"
  | "nothing_to_do"
  | "already_running"
  | "preset_unavailable"
  | "cancelled";

export interface RunResult {
  outcome: RunOutcome;
  message?: string;
}

export interface ProcessingRunSnapshot {
  phase: RunPhase;
  items: readonly PhotoRunState[];
  /** Style of the run (null while idle). */
  preset: ProcessingPresetId | null;
}

export const IDLE_RUN: ProcessingRunSnapshot = { phase: "idle", items: [], preset: null };

export interface StartOptions {
  /** Send every photo again, also those already processed with the style or rejected. */
  reprocess?: boolean;
}

/**
 * What a run does with photos whose outcome is known (photo-checks.ts):
 * - skip_known:   not sent again (a new run)
 * - skip_results: only saved results count, rejections are sent again (`retry`)
 * - skip_checked: only outcomes of this visit count (a `reprocess` run resumed
 *                 after a reload: results saved before it are sent again)
 * - all:          everything is sent again (`reprocess`)
 */
type KnownPolicy = "skip_known" | "skip_results" | "skip_checked" | "all";

const CONCURRENCY = 3;

const GENERIC_FAILURE = "Bearbeitung fehlgeschlagen.";
const RESUME_FAILED_MESSAGE = "Die Bearbeitung wurde unterbrochen. Bitte erneut versuchen.";

interface VehicleRun {
  vehicleId: string;
  snapshot: ProcessingRunSnapshot;
  /** `start` / `retry` calls in flight – the phase is "done" once all ended. */
  active: number;
  /** Bumped by `start` / `reset` so a retry of an older run cannot revive it. */
  generation: number;
  /** Aborts the uploads and polls of the current generation (`reset`, sign-out). */
  controller: AbortController;
  /** Started with `reprocess` (kept on the device for `resume`). */
  reprocess: boolean;
  /** When the run started (ISO). */
  startedAt: string;
  /** What was last kept on the device (storedKey) – rewritten only when a row finished. */
  storedKey: string | null;
}

function isFinished(item: PhotoRunState): boolean {
  return item.status === "complete" || item.status === "failed";
}

/** The run's rows as kept on the device: rows on their way are processed again after a reload. */
function toStoredItems(items: readonly PhotoRunState[]): StoredRunItem[] {
  return items.map(({ photoId, shotKey, title, shotOrder, treatment, status, error, errorCode, warnings }) =>
    status === "complete" || status === "failed"
      ? { photoId, shotKey, title, shotOrder, treatment, status, error, errorCode, warnings }
      : { photoId, shotKey, title, shotOrder, treatment, status: "open", error: null, errorCode: null, warnings: [] },
  );
}

/** Changes when a row is added, removed, follows another photo or finishes (not with its progress). */
function storedKey(snapshot: ProcessingRunSnapshot): string {
  return snapshot.items.map((item) => `${item.photoId}:${isFinished(item) ? item.status : "open"}`).join("|");
}

/** A row of the run for `photo`, waiting to be processed. */
function waitingItem(photo: VehiclePhotoWithUrls, treatment: ShotTreatment): PhotoRunState {
  return {
    photoId: photo.id,
    shotKey: photo.shotKey,
    title: photo.title,
    shotOrder: photo.shotOrder,
    treatment,
    status: "waiting",
    progress: 0,
    error: null,
    errorCode: null,
    warnings: [],
  };
}

/** The signed-in user's id, null when signed out, undefined while still unknown. */
function sessionUser(auth: AuthState): string | null | undefined {
  if (auth.status === "loading") return undefined;
  return auth.status === "authenticated" ? auth.user.id : null;
}

/** The newest finished check of a photo of the shot taken after `takenAt` (photo-checks.ts). */
function newestSettledCheck(vehicleId: string, shotKey: string, takenAt: string): PhotoCheck | null {
  let newest: PhotoCheck | null = null;
  for (const check of photoChecks.getSnapshot().checks.values()) {
    if (check.vehicleId !== vehicleId || check.shotKey !== shotKey) continue;
    if (check.status === "waiting" || check.status === "checking" || !isOlderCapture(takenAt, check.takenAt)) continue;
    if (!newest || isOlderCapture(newest.takenAt, check.takenAt)) newest = check;
  }
  return newest;
}

export class ProcessingRuns {
  private readonly runs = new Map<string, VehicleRun>();
  private readonly listeners = new Set<() => void>();
  private watchingSession = false;
  private followingChecks = false;

  /* ---------- useSyncExternalStore API ---------- */

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (vehicleId: string): ProcessingRunSnapshot => this.runs.get(vehicleId)?.snapshot ?? IDLE_RUN;

  /* ---------- actions ---------- */

  /**
   * Processes the template shots of the vehicle – the photos it has right
   * now (`photos`: the list on screen, used until the current one is read);
   * refused while a run of this vehicle is in flight.
   */
  async start(
    vehicleId: string,
    photos: readonly VehiclePhotoWithUrls[],
    preset: ProcessingPresetId,
    accessCode: string | null,
    options: StartOptions = {},
  ): Promise<RunResult> {
    if (!isProcessingPresetAvailable(preset)) {
      return { outcome: "preset_unavailable", message: PRESET_UNAVAILABLE_MESSAGE };
    }
    const run = this.run(vehicleId);
    if (run.active > 0) return { outcome: "already_running" };
    const { template } = getAppServices();
    const shown = selectPhotosForProcessing(template, photos);
    if (shown.length === 0) return { outcome: "nothing_to_do" };
    this.watchSession();

    run.generation += 1;
    const generation = run.generation;
    run.controller = new AbortController();
    run.active = 1;
    run.reprocess = options.reprocess ?? false;
    run.startedAt = new Date().toISOString();
    const toItems = (list: readonly VehiclePhotoWithUrls[]) =>
      list.map((photo) => waitingItem(photo, getShotTreatment(template, photo.shotKey) ?? "original_environment"));
    this.set(run, { phase: "running", preset, items: toItems(shown) });

    const selected = selectPhotosForProcessing(template, await this.currentPhotos(vehicleId, photos));
    // Reset (or signed out) meanwhile: this run no longer exists.
    if (run.generation !== generation) return { outcome: "cancelled" };
    if (selected.length === 0) {
      this.resetRun(run);
      return { outcome: "nothing_to_do" };
    }
    this.set(run, { phase: "running", preset, items: toItems(selected) });
    const policy: KnownPolicy = options.reprocess ? "all" : "skip_known";
    const accessDenied = await this.processAll(run, generation, selected, preset, accessCode, policy);
    return this.complete(run, generation, accessDenied);
  }

  /**
   * Processes shots of the run again – their new photos after "Foto neu
   * aufnehmen", or the same ones after a temporary failure – at most
   * CONCURRENCY at a time; the run's other results stay as they are.
   */
  async retry(
    vehicleId: string,
    photos: readonly VehiclePhotoWithUrls[],
    preset: ProcessingPresetId,
    accessCode: string | null,
  ): Promise<RunResult> {
    if (!isProcessingPresetAvailable(preset)) {
      return { outcome: "preset_unavailable", message: PRESET_UNAVAILABLE_MESSAGE };
    }
    const run = this.run(vehicleId);
    /** The finished rows to process again (as they were), by shot key. */
    const finished = new Map<string, PhotoRunState>();
    const shown = new Map<string, VehiclePhotoWithUrls>();
    let onItsWay = false;
    for (const photo of photos) {
      const item = run.snapshot.items.find((entry) => entry.shotKey === photo.shotKey);
      if (item && isFinished(item)) {
        finished.set(photo.shotKey, item);
        shown.set(photo.shotKey, photo);
      } else if (item) onItsWay = true; // e.g. tapped twice
    }
    if (finished.size === 0) return { outcome: onItsWay ? "already_running" : "nothing_to_do" };
    this.watchSession();

    const generation = run.generation;
    run.active = Math.max(0, run.active) + 1;
    const queue = (targets: ReadonlyMap<string, VehiclePhotoWithUrls>) =>
      this.set(run, {
        ...run.snapshot,
        phase: "running",
        items: run.snapshot.items.map((item) => {
          const photo = targets.get(item.shotKey);
          return photo ? { ...waitingItem(photo, item.treatment), shotOrder: item.shotOrder } : item;
        }),
      });
    queue(shown);

    // The shot's current photo: a newer one may have been saved meanwhile.
    const current = new Map((await this.currentPhotos(vehicleId, photos)).map((photo) => [photo.shotKey, photo]));
    if (run.generation !== generation) return { outcome: "cancelled" };
    const targets = new Map<string, VehiclePhotoWithUrls>();
    const restore = new Map<string, PhotoRunState>();
    for (const [shotKey, item] of finished) {
      const photo = current.get(shotKey);
      // Gone, or exactly this photo was already processed: nothing to do for the shot.
      if (!photo || (item.status === "complete" && item.photoId === photo.id)) restore.set(shotKey, item);
      else targets.set(shotKey, photo);
    }
    if (restore.size > 0) {
      this.set(run, {
        ...run.snapshot,
        items: run.snapshot.items.map((item) => restore.get(item.shotKey) ?? item),
      });
    }
    queue(targets);
    const accessDenied = await this.processAll(
      run,
      generation,
      [...targets.values()],
      preset,
      accessCode,
      "skip_results",
    );
    return this.complete(run, generation, accessDenied);
  }

  /**
   * Continues the vehicle's run that was going on before the page was
   * reloaded (or the tab discarded): kept on the device while it ran. Its
   * finished rows stay as they were; every other row is processed again with
   * the shot's current photo – a job submitted before the reload is polled,
   * not sent again (photo-checks.ts). null: nothing to continue (no stored
   * run, or a run of the vehicle is shown already).
   */
  async resume(vehicleId: string): Promise<RunResult | null> {
    const run = this.run(vehicleId);
    if (run.active > 0 || run.snapshot.phase !== "idle") return null;
    const stored = readStoredRun(vehicleId);
    if (!stored) return null;
    if (!isProcessingPresetAvailable(stored.preset)) {
      forgetRun(vehicleId);
      return null;
    }
    this.watchSession();
    run.generation += 1;
    const generation = run.generation;
    run.controller = new AbortController();
    run.active = 1;
    run.reprocess = stored.reprocess;
    run.startedAt = stored.startedAt;
    this.set(run, {
      phase: "running",
      preset: stored.preset,
      items: stored.items.map((item) => ({
        ...item,
        status: item.status === "open" ? "waiting" : item.status,
        progress: item.status === "complete" ? 1 : 0,
      })),
    });

    // The shots' current photos: a retake may have been saved meanwhile.
    let current: readonly VehiclePhotoWithUrls[] | null = null;
    try {
      const { backend } = getAppServices();
      current = await backend.data.resolvePhotoUrls(await backend.data.listPhotos(vehicleId));
    } catch {
      // Not readable right now: the open rows fail (they can be processed again from the list).
    }
    if (run.generation !== generation) return { outcome: "cancelled" };
    const photos: VehiclePhotoWithUrls[] = [];
    const items = run.snapshot.items.flatMap((item): PhotoRunState[] => {
      if (isFinished(item)) return [item];
      if (current === null) return [{ ...item, status: "failed", error: RESUME_FAILED_MESSAGE }];
      const photo = current.find((entry) => entry.shotKey === item.shotKey);
      if (!photo) return []; // deleted meanwhile
      photos.push(photo);
      return [{ ...item, photoId: photo.id, title: photo.title }];
    });
    this.set(run, { ...run.snapshot, items });
    const policy: KnownPolicy = stored.reprocess ? "skip_checked" : "skip_known";
    const accessDenied = await this.processAll(run, generation, photos, stored.preset, readStoredAccessCode(), policy);
    // The stored code is no longer valid: the processing page asks for it again.
    if (accessDenied !== null && run.generation === generation) storeAccessCode(null);
    return this.complete(run, generation, accessDenied);
  }

  /** Back to the style selection; uploads and polls still on their way are aborted. */
  reset(vehicleId: string): void {
    const run = this.runs.get(vehicleId);
    if (run) this.resetRun(run);
  }

  /** Ends every run (the session ended or another user signed in). */
  clear(): void {
    for (const run of this.runs.values()) this.resetRun(run);
  }

  /* ---------- internals ---------- */

  /**
   * The vehicle's photos as saved right now (template order); `fallback`
   * (the list on screen) if they cannot be read – processing then reports
   * its own error for each photo.
   */
  private async currentPhotos(
    vehicleId: string,
    fallback: readonly VehiclePhotoWithUrls[],
  ): Promise<readonly VehiclePhotoWithUrls[]> {
    try {
      const { backend, template } = getAppServices();
      const photos = await backend.data.listPhotos(vehicleId);
      return await backend.data.resolvePhotoUrls(sortPhotosByShotOrder(template, photos));
    } catch {
      return fallback;
    }
  }

  private run(vehicleId: string): VehicleRun {
    let run = this.runs.get(vehicleId);
    if (!run) {
      run = {
        vehicleId,
        snapshot: IDLE_RUN,
        active: 0,
        generation: 0,
        controller: new AbortController(),
        reprocess: false,
        startedAt: new Date().toISOString(),
        storedKey: null,
      };
      this.runs.set(vehicleId, run);
    }
    return run;
  }

  private resetRun(run: VehicleRun): void {
    run.generation += 1;
    run.active = 0;
    run.controller.abort();
    run.controller = new AbortController();
    this.set(run, IDLE_RUN);
  }

  /**
   * Runs belong to the signed-in user: on a shared device the next person
   * must neither see the previous user's runs nor keep them going.
   */
  private watchSession(): void {
    this.followChecks();
    if (this.watchingSession) return;
    this.watchingSession = true;
    const { authStore } = getAppServices();
    let user = sessionUser(authStore.getSnapshot());
    authStore.subscribe(() => {
      const next = sessionUser(authStore.getSnapshot());
      if (next === undefined || next === user) return;
      const changed = user !== undefined;
      user = next;
      if (changed) this.clear();
    });
  }

  /**
   * A retake saved after its shot was processed is checked automatically
   * (photo-checks.ts): its result replaces the shot's row, so the run shows
   * it as done (or rejected) instead of offering to process it again. The
   * same goes for a failed row whose photo was checked later (e.g. it failed
   * because the processor was gone and was checked once it answered again).
   */
  private followChecks(): void {
    if (this.followingChecks) return;
    this.followingChecks = true;
    photoChecks.onSettled((check) => this.adoptCheck(check));
  }

  private adoptCheck(check: PhotoCheck): void {
    const run = this.runs.get(check.vehicleId);
    if (!run || run.snapshot.phase === "idle" || run.snapshot.preset !== check.preset) return;
    const item = run.snapshot.items.find((entry) => entry.shotKey === check.shotKey);
    // Only a finished row (a row on its way follows its own photo): of an older
    // photo, or a failed row of this photo.
    if (!item || !isFinished(item) || (item.photoId === check.photoId && item.status !== "failed")) return;
    let patch: Partial<PhotoRunState>;
    if (check.status === "ok" || (check.status === "retake" && check.errorCode === null)) {
      patch = { status: "complete", progress: 1, error: null, errorCode: null, warnings: check.warnings };
    } else if (check.status === "retake") {
      patch = { status: "failed", error: check.message ?? GENERIC_FAILURE, errorCode: check.errorCode, warnings: [] };
    } else {
      return; // not checked: the new photo can be processed from the run
    }
    this.set(run, {
      ...run.snapshot,
      items: run.snapshot.items.map((entry) =>
        entry === item ? { ...entry, ...patch, photoId: check.photoId, title: check.title } : entry,
      ),
    });
  }

  /**
   * Processes `photos` with at most CONCURRENCY in flight. Returns the German
   * message when the access code was refused (the remaining photos are skipped).
   */
  private async processAll(
    run: VehicleRun,
    generation: number,
    photos: readonly VehiclePhotoWithUrls[],
    preset: ProcessingPresetId,
    accessCode: string | null,
    policy: KnownPolicy,
  ): Promise<string | null> {
    // This generation's controller – `reset` replaces run.controller.
    const { controller } = run;
    let accessDenied: string | null = null;
    const queue = [...photos];
    const worker = async () => {
      for (let queued = queue.shift(); queued && !controller.signal.aborted; queued = queue.shift()) {
        const photo = await this.photoToSubmit(run, generation, queued);
        // Reset, signed out or the access code refused meanwhile: nothing more is sent.
        if (!photo || controller.signal.aborted) continue;
        try {
          await this.processOne(run, generation, photo, preset, accessCode, controller.signal, policy);
          // A retake of the shot was checked meanwhile: its result is the shot's result now.
          const newer = newestSettledCheck(run.vehicleId, photo.shotKey, photo.takenAt);
          if (newer && run.generation === generation) this.adoptCheck(newer);
        } catch (error) {
          accessDenied ??= toUserMessage(error);
          controller.abort(); // stop the other uploads – the code is wrong for all of them
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
    return accessDenied;
  }

  /**
   * The photo to submit for `queued`, read right before it is submitted: the
   * shot's photo as saved now. A retake saved while the shot waited in the
   * queue replaces the photo it archived, and the shot's row follows it.
   * null: that photo already has a row in this run (processed or on its way)
   * – it is not processed twice, the queued row is dropped. The queued photo
   * itself when the photos cannot be read or the shot has none right now
   * (e.g. its retake is just being saved) – the processor's answer tells.
   */
  private async photoToSubmit(
    run: VehicleRun,
    generation: number,
    queued: VehiclePhotoWithUrls,
  ): Promise<VehiclePhotoWithUrls | null> {
    let photo: VehiclePhotoWithUrls | undefined;
    try {
      const { backend } = getAppServices();
      const current = (await backend.data.listPhotos(run.vehicleId)).find((entry) => entry.shotKey === queued.shotKey);
      if (current && current.id !== queued.id) [photo] = await backend.data.resolvePhotoUrls([current]);
    } catch {
      // Not readable right now: the queued photo.
    }
    // Unchanged – or the run was reset meanwhile (then nothing is submitted).
    if (!photo || run.generation !== generation) return queued;
    if (run.snapshot.items.some((item) => item.photoId === photo.id)) {
      this.set(run, { ...run.snapshot, items: run.snapshot.items.filter((item) => item.photoId !== queued.id) });
      return null;
    }
    this.updateItem(run, generation, queued.id, { photoId: photo.id, title: photo.title });
    return photo;
  }

  /** Processes one photo and records its outcome; throws only AccessCodeRequiredError. */
  private async processOne(
    run: VehicleRun,
    generation: number,
    photo: VehiclePhotoWithUrls,
    preset: ProcessingPresetId,
    accessCode: string | null,
    signal: AbortSignal,
    policy: KnownPolicy,
  ): Promise<void> {
    const update = (patch: Partial<PhotoRunState>) => this.updateItem(run, generation, photo.id, patch);
    // Checked automatically after the capture (or processed before): not sent again.
    const known =
      policy === "all"
        ? null
        : policy === "skip_checked"
          ? photoChecks.knownOutcome({ ...photo, processedStoragePath: null }, preset)
          : photoChecks.knownOutcome(photo, preset, { ignoreFailures: policy === "skip_results" });
    if (known?.status === "complete") {
      update({ status: "complete", progress: 1, error: null, errorCode: null, warnings: known.warnings });
      return;
    }
    if (known?.status === "failed") {
      update({ status: "failed", error: known.error, errorCode: known.errorCode });
      return;
    }
    try {
      // Joins the automatic check of the photo if it is still running.
      const { warnings } = await photoChecks.process(photo, preset, {
        accessCode,
        signal,
        onStep: (status, progress) => update(progress === undefined ? { status } : { status, progress }),
      });
      update({ status: "complete", progress: 1, error: null, errorCode: null, warnings });
    } catch (error) {
      if (error instanceof AccessCodeRequiredError) throw error;
      update({
        status: "failed",
        error: toUserMessage(error, GENERIC_FAILURE),
        errorCode: error instanceof ProcessingJobFailedError ? error.errorCode : null,
      });
    }
  }

  /** Ends one start/retry; the last one to end syncs the vehicle status and shows the summary. */
  private async complete(run: VehicleRun, generation: number, accessDenied: string | null): Promise<RunResult> {
    // Reset (or signed out) meanwhile: this run no longer exists.
    if (run.generation !== generation) return { outcome: "cancelled" };
    if (accessDenied !== null) {
      // The stored access code is no longer valid: back to the start screen.
      this.resetRun(run);
      return { outcome: "access_denied", message: accessDenied };
    }
    run.active -= 1;
    if (run.active > 0) return { outcome: "done" };
    const { backend, template } = getAppServices();
    try {
      // Only when every required shot really has a processed version.
      await markProcessedIfDone(backend.data, template, run.vehicleId);
    } catch {
      // Status update is retried on the next run.
    }
    // A retry may have started in the meantime.
    if (run.generation === generation && run.active === 0) this.set(run, { ...run.snapshot, phase: "done" });
    return { outcome: "done" };
  }

  private updateItem(run: VehicleRun, generation: number, photoId: string, patch: Partial<PhotoRunState>): void {
    if (run.generation !== generation) return;
    this.set(run, {
      ...run.snapshot,
      items: run.snapshot.items.map((item) => (item.photoId === photoId ? { ...item, ...patch } : item)),
    });
  }

  private set(run: VehicleRun, snapshot: ProcessingRunSnapshot): void {
    run.snapshot = snapshot;
    this.store(run);
    for (const listener of this.listeners) listener();
  }

  /** Keeps a running run on the device (for `resume` after a reload); a finished or reset one is forgotten. */
  private store(run: VehicleRun): void {
    const { snapshot } = run;
    if (snapshot.phase !== "running" || snapshot.preset === null) {
      if (run.storedKey !== null) forgetRun(run.vehicleId);
      run.storedKey = null;
      return;
    }
    const key = storedKey(snapshot);
    if (key === run.storedKey) return;
    run.storedKey = key;
    storeRun({
      vehicleId: run.vehicleId,
      preset: snapshot.preset,
      reprocess: run.reprocess,
      startedAt: run.startedAt,
      items: toStoredItems(snapshot.items),
    });
  }
}

/** The runs of this browser tab (module-level, shared by every processing screen). */
export const processingRuns = new ProcessingRuns();
