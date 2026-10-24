export interface SseFrame {
  event: string;
  data: string;
  id?: string;
}

/** Small WHATWG-compatible parser for one authenticated jobs event stream. */
export class SseParser {
  private buffer = '';
  private eventName = '';
  private dataLines: string[] = [];
  private lastEventId: string | undefined;

  push(chunk: string, final = false): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    let start = 0;
    for (let i = 0; i < this.buffer.length; i += 1) {
      const char = this.buffer[i];
      if (char !== '\n' && char !== '\r') continue;
      if (char === '\r' && i + 1 === this.buffer.length && !final) break;
      const line = this.buffer.slice(start, i);
      if (char === '\r' && this.buffer[i + 1] === '\n') i += 1;
      start = i + 1;
      this.readLine(line, frames);
    }
    this.buffer = this.buffer.slice(start);
    if (final) {
      if (this.buffer) this.readLine(this.buffer, frames);
      this.buffer = '';
      this.dispatch(frames);
    }
    return frames;
  }

  private readLine(line: string, frames: SseFrame[]): void {
    if (line === '') {
      this.dispatch(frames);
      return;
    }
    if (line.startsWith(':')) return;
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.eventName = value;
    else if (field === 'data') this.dataLines.push(value);
    else if (field === 'id' && !value.includes('\0')) this.lastEventId = value;
  }

  private dispatch(frames: SseFrame[]): void {
    if (this.dataLines.length > 0) {
      frames.push({
        event: this.eventName || 'message',
        data: this.dataLines.join('\n'),
        ...(this.lastEventId !== undefined ? { id: this.lastEventId } : {}),
      });
    }
    this.eventName = '';
    this.dataLines = [];
  }
}
