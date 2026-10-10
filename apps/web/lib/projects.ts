'use client';
/** Project list/create/open for both modes (live API through the BFF, or the local DEMO store). */

import type { ProjectType } from '@shared/schema.ts';
import { api, demo, type Mode, type ProjectOut, type ProjectRow } from './client';

export async function listProjects(mode: Mode): Promise<ProjectRow[]> {
  if (mode === 'demo') return demo.list();
  return (await api<{ items: ProjectRow[] }>('/editor/projects')).items;
}

export async function createProject(mode: Mode, type: ProjectType, title: string, preset: string | null,
                                    template: string | null): Promise<ProjectOut> {
  if (mode === 'demo') return demo.create(type, title, preset, template);
  return api<ProjectOut>('/editor/projects', { method: 'POST', body: { type, title, preset: preset ?? undefined,
                                                                         template: template ?? undefined } });
}

export async function deleteProject(mode: Mode, id: string): Promise<void> {
  if (mode === 'demo') return demo.remove(id);
  await api(`/editor/projects/${id}`, { method: 'DELETE' });
}

export const TYPE_LABEL: Record<ProjectType, string> = {
  video: 'Video', photo: 'Tasarım', social: 'Sosyal', film: 'Film', episode: 'Bölüm',
};
