import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { AccountApp } from './portal';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('account portal', () => {
  it('sends anonymous users to the server-side OIDC login route', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    render(<AccountApp />);
    expect((await screen.findByRole('link', { name: 'Continue to sign in' }))
      .getAttribute('href')).toBe('/auth/login');
    expect(localStorage.length).toBe(0);
  });
});
