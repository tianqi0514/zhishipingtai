import type { CSSProperties, RefObject } from 'react';
import { YjsPlugin } from '@platejs/yjs/react';
import { type CursorOverlayData, useRemoteCursorOverlayPositions } from '@slate-yjs/react';
import { useEditorContainerRef, usePluginOption } from 'platejs/react';

type CursorData = { color: string; name: string };

export function RemoteCursorOverlay() {
  const isSynced = usePluginOption(YjsPlugin, '_isSynced');
  if (!isSynced) return null;
  return <RemoteCursorOverlayContent />;
}

function RemoteCursorOverlayContent() {
  const containerRef = useEditorContainerRef();
  const [cursors] = useRemoteCursorOverlayPositions<CursorData>({ containerRef: containerRef as RefObject<HTMLDivElement> });
  return <>{cursors.map((cursor) => <RemoteSelection key={cursor.clientId} {...cursor} />)}</>;
}

function RemoteSelection({ caretPosition, data, selectionRects }: CursorOverlayData<CursorData>) {
  if (!data) return null;
  const selection: CSSProperties = { backgroundColor: `${data.color}55` };
  return <>
    {selectionRects.map((position, index) => <div key={index} className="remote-selection" style={{ ...selection, ...position }} />)}
    {caretPosition && <div className="remote-caret" style={{ ...caretPosition, background: data.color }}><span style={{ background: data.color }}>{data.name}</span></div>}
  </>;
}
