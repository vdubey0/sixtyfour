import { useEffect, useRef, useState, type ReactNode } from 'react';

export function ResizableInspector({ children }: { children: ReactNode }) {
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [preferredWidth, setPreferredWidth] = useState(() =>
    window.innerWidth <= 760 ? 300 : Math.floor(window.innerWidth * 0.25),
  );
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [resizing, setResizing] = useState(false);
  const mobile = viewportWidth <= 760;
  const maximum = mobile ? Math.min(300, viewportWidth) : Math.floor(viewportWidth * 0.35);
  const minimum = Math.min(240, maximum);
  const width = Math.max(minimum, Math.min(preferredWidth, maximum));
  const resize = (value: number) => setPreferredWidth(Math.max(minimum, Math.min(value, maximum)));

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return <aside className={`inspector resizable-inspector${resizing ? ' is-resizing' : ''}`} style={{ width }}>
    {!mobile && <div
      className="inspector-resize-handle"
      role="separator"
      aria-label="Resize block settings"
      aria-orientation="vertical"
      aria-valuemin={minimum}
      aria-valuemax={maximum}
      aria-valuenow={width}
      tabIndex={0}
      title="Drag left to widen · Arrow keys to resize"
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { x: event.clientX, width };
        setResizing(true);
      }}
      onPointerMove={event => {
        if (drag.current) resize(drag.current.width + drag.current.x - event.clientX);
      }}
      onPointerUp={event => {
        event.currentTarget.releasePointerCapture(event.pointerId);
        drag.current = null;
        setResizing(false);
      }}
      onLostPointerCapture={() => { drag.current = null; setResizing(false); }}
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        resize(event.key === 'Home' ? minimum : event.key === 'End' ? maximum : width + (event.key === 'ArrowLeft' ? 10 : -10));
      }}
    />}
    <div className="inspector-content">{children}</div>
  </aside>;
}
