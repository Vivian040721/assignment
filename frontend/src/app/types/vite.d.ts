interface ImportMeta {
  readonly hot?: {
    dispose(callback: () => void): void;
  };
}
