'use client';

import { use } from 'react';
import Editor from '@/components/editor/Editor';

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Editor id={id} />;
}
