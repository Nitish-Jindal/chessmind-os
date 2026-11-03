/** Identifies async work owned by one mounted session instance. */
export class SessionGeneration {
  private generation = 0;
  private active = false;

  activate(): number {
    this.active = true;
    return ++this.generation;
  }

  capture(): number {
    return this.generation;
  }

  isCurrent(generation: number): boolean {
    return this.active && this.generation === generation;
  }

  invalidate(generation: number): void {
    if (!this.isCurrent(generation)) return;
    this.active = false;
    this.generation++;
  }
}
