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
 * way) and would make the run process the photo it replaced. The adapter
 * follows the data backend (Supabase contract or demo upload); only real
 * results are saved, with the processor's hints (`warnings`), failures save
 * nothing. Shots the processor rejected (e.g. quality gate) stay failed
 * while the rest of the run continues; `retry` processes shots again – the
 * new photos after "Foto neu aufnehmen" or the same photo after a temporary
 * failure.
 *
 * Runs belong to the signed-in user: when the session ends or another user
 * signs in (shared devices), every run is stopped – its uploads and polls
 * are aborted – and the next person starts with the style selection.
 */
import { getAppServices } from "@/lib/app-services";
import type { AuthState } from "@/lib/auth/auth-store";
import type { ProcessingPresetId, ProcessingWarning, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { toUserMessage } from "@/lib/errors";
import { sortPhotosByShotOrder } from "@/lib/shots/shot-progress";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";
import { ProcessingJobFailedError, processPhoto, type PhotoStep } from "./photo-processing";
import { PRESET_UNAVAILABLE_MESSAGE, isProcessingPresetAvailable } from "./presets";
import { AccessCodeRequiredError } from "./processing-client";
import { getShotTreatment, selectPhotosForProcessing, type ShotTreatment } from "./shot-treatment";

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

const CONCURRENCY = 3;

const GENERIC_FAILURE = "Bearbeitung fehlgeschlagen.";

interface VehicleRun {
  vehicleId: string;
  snapshot: ProcessingRunSnapshot;
  /** `start` / `retry` calls in flight – the phase is "done" once all ended. */
  active: number;
  /** Bumped by `start` / `reset` so a retry of an older run cannot revive it. */
  generation: number;
  /** Aborts the uploads and polls of the current generation (`reset`, sign-out). */
  controller: AbortController;
}

function isFinished(item: PhotoRunState): boolean {
  return item.status === "complete" || item.status === "failed";
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

export class ProcessingRuns {
  private readonly runs = new Map<string, VehicleRun>();
  private readonly listeners = new Set<() => void>();
  private watchingSession = false;

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
    const accessDenied = await this.processAll(run, generation, selected, preset, accessCode);
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
    const accessDenied = await this.processAll(run, generation, [...targets.values()], preset, accessCode);
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
      run = { vehicleId, snapshot: IDLE_RUN, active: 0, generation: 0, controller: new AbortController() };
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
   * Processes `photos` with at most CONCURRENCY in flight. Returns the German
   * message when the access code was refused (the remaining photos are skipped).
   */
  private async processAll(
    run: VehicleRun,
    generation: number,
    photos: readonly VehiclePhotoWithUrls[],
    preset: ProcessingPresetId,
    accessCode: string | null,
  ): Promise<string | null> {
    // This generation's controller – `reset` replaces run.controller.
    const { controller } = run;
    let accessDenied: string | null = null;
    const queue = [...photos];
    const worker = async () => {
      for (let photo = queue.shift(); photo && !controller.signal.aborted; photo = queue.shift()) {
        try {
          await this.processOne(run, generation, photo, preset, accessCode, controller.signal);
        } catch (error) {
          accessDenied ??= toUserMessage(error);
          controller.abort(); // stop the other uploads – the code is wrong for all of them
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));
    return accessDenied;
  }

  /** Processes one photo and records its outcome; throws only AccessCodeRequiredError. */
  private async processOne(
    run: VehicleRun,
    generation: number,
    photo: VehiclePhotoWithUrls,
    preset: ProcessingPresetId,
    accessCode: string | null,
    signal: AbortSignal,
  ): Promise<void> {
    const update = (patch: Partial<PhotoRunState>) => this.updateItem(run, generation, photo.id, patch);
    const { backend } = getAppServices();
    try {
      const { warnings } = await processPhoto(photo, preset, {
        mode: backend.mode,
        data: backend.data,
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
    for (const listener of this.listeners) listener();
  }
}

/** The runs of this browser tab (module-level, shared by every processing screen). */
export const processingRuns = new ProcessingRuns();
