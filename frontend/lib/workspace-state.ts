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
