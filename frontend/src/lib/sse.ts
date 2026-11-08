// A minimal Server-Sent Events reader for fetch() responses.
//
// The browser's EventSource only supports GET and can't send an Authorization header, so the
// query stream (POST /api/query) is read from fetch's body instead. Follows the parts of the
// SSE spec the backend uses: events separated by a blank line, one or more `data:` lines each.

/** Split one event block into its joined `data:` payload (or null if it has none). */
function eventData(block: string): string | null {
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  return data.length ? data.join('\n') : null
}

/** Read an SSE body and call `onData` with each event's data, in order. */
export async function readEventStream(
  body: ReadableStream<Uint8Array>,
  onData: (data: string) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const flush = (final: boolean) => {
    buffer = buffer.replace(/\r\n?/g, '\n') // the spec allows CRLF and CR line endings
    let end: number
    while ((end = buffer.indexOf('\n\n')) !== -1) {
      const data = eventData(buffer.slice(0, end))
      buffer = buffer.slice(end + 2)
      if (data !== null) onData(data)
    }
    // A stream may end without a trailing blank line; don't drop its last event.
    if (final && buffer.trim()) {
      const data = eventData(buffer)
      buffer = ''
      if (data !== null) onData(data)
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    flush(false)
  }
  buffer += decoder.decode()
  flush(true)
}
