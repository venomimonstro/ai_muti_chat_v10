// A late network response must never replace a newer navigation choice.
export function createLatestRequest() {
  let revision = 0;
  return {begin: () => ++revision, isCurrent: (ticket: number) => ticket === revision, invalidate: () => ++revision};
}

// Serialize PUT and DELETE for each conversation. Clearing a sent draft must not
// race an earlier autosave that is still in flight.
export function createDraftWriter() {
  const writes = new Map<string, Promise<unknown>>();
  return function enqueue<T>(id: string, write: () => Promise<T>): Promise<T> {
    const next = (writes.get(id) ?? Promise.resolve()).catch(() => undefined).then(write);
    writes.set(id, next);
    void next.finally(() => {if (writes.get(id) === next) writes.delete(id);}).catch(() => undefined);
    return next;
  };
}

export function attachmentReadiness(files: Array<{status: string}>) {
  const failed = files.some(file => ["failed", "deleted", "deleting"].includes(file.status));
  const pending = files.some(file => !["ready", "partial", "failed", "deleted", "deleting"].includes(file.status));
  return {blocked: failed || pending, message: failed ? "Не удалось обработать файл. Уберите его или загрузите заново." : pending ? "Обрабатываем файлы. Можно пока написать сообщение." : ""};
}

// Persist only selected IDs. File metadata is fetched through authenticated API
// before restoration; each conversation owns its selection independently.
export function createAttachmentDrafts<T extends {id: string}>(storage: {
  getItem(key: string): string | null; setItem(key: string, value: string): void;
}) {
  const selections = new Map<string, T[]>();
  const key = (id: string) => `aiws:attachment-draft:${id}`;
  return {
    ids(id: string): string[] {
      if (selections.has(id)) return selections.get(id)!.map(file => file.id);
      try {
        const saved: unknown = JSON.parse(storage.getItem(key(id)) ?? "[]");
        return Array.isArray(saved) ? [...new Set(saved.filter((value): value is string => typeof value === "string" && value.length > 0))].slice(0, 4) : [];
      } catch { return []; }
    },
    get(id: string): T[] { return [...(selections.get(id) ?? [])]; },
    update(id: string, update: (files: T[]) => T[]): T[] {
      const next = [...new Map(update([...(selections.get(id) ?? [])]).map(file => [file.id, file])).values()].slice(0, 4);
      selections.set(id, next);
      try { storage.setItem(key(id), JSON.stringify(next.map(file => file.id))); } catch {}
      return [...next];
    },
  };
}
