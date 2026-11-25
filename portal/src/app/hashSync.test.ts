import { describe, expect, it } from 'vitest';
import { hashToPath, pathToHash } from './hashSync';

describe('plain-hash routing for the sandboxed preview', () => {
  it('maps paths to #page or #page-id (never #/path)', () => {
    expect(pathToHash('/pipelines')).toBe('pipelines');
    expect(pathToHash('/builder/pl_ap')).toBe('builder-pl_ap');
    expect(pathToHash('/review/rv1')).toBe('review-rv1');
    expect(pathToHash('/')).toBe('pipelines');
  });
  it('maps hashes back to router paths', () => {
    expect(hashToPath('#builder-pl_ap')).toBe('/builder/pl_ap');
    expect(hashToPath('#settings')).toBe('/settings');
    expect(hashToPath('')).toBe('/pipelines');
    expect(hashToPath('#/connections')).toBe('/connections');
  });
});
