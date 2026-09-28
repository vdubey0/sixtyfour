import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { BlockInstance, BlockType, Connection } from "../lib/types";
import { BlockNode } from "./BlockNode";

const NODE_W = 160; // matches BlockNode minWidth

type CanvasProps = {
  blocks: BlockInstance[];
  setBlocks: React.Dispatch<React.SetStateAction<BlockInstance[]>>;
  blockTypes: BlockType[];

  connections: Connection[];
  setConnections: React.Dispatch<React.SetStateAction<Connection[]>>;

  selectedSourceId: string | null;
  setSelectedSourceId: React.Dispatch<React.SetStateAction<string | null>>;

  runStatusById: Record<string, "idle" | "pending" | "running" | "done">;
};

type DragState = {
  blockId: string;
  // How far the cursor is from the block's center in canvas coords
  offsetX: number;
  offsetY: number;

  startClientX: number;
  startClientY: number;
  moved: boolean;
};

export function Canvas({
  blocks,
  setBlocks,
  blockTypes,
  connections,
  setConnections,
  selectedSourceId,
  setSelectedSourceId,
  runStatusById
}: CanvasProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [canvasRect, setCanvasRect] = useState<DOMRect | null>(null);
  const [dragging, setDragging] = useState<DragState | null>(null);
  const suppressNextClickRef = useRef(false);

  // NEW: track hovered connection so we can highlight it
  const [hoveredConnectionId, setHoveredConnectionId] = useState<string | null>(null);

  // Build lookup: type -> metadata (label, color, etc.)
  // useMemo avoids rebuilding map unless blockTypes changes.
  const metaByType = useMemo(() => {
    return new Map(blockTypes.map((bt) => [bt.type, bt]));
  }, [blockTypes]);

  const blockById = useMemo(() => {
    return new Map(blocks.map((b) => [b.id, b]));
  }, [blocks]);

  const handleBlockClick = (blockId: string) => {
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false;
      return;
    }
    // First click selects the source
    if (selectedSourceId === null) {
      setSelectedSourceId(blockId);
      return;
    }

    // Clicking the same block again cancels selection
    if (selectedSourceId === blockId) {
      setSelectedSourceId(null);
      return;
    }

    // NEW: enforce one outgoing per source and one incoming per target
    setConnections((prev) => {
      const filtered = prev.filter(
        (c) => c.fromId !== selectedSourceId && c.toId !== blockId
      );

      return [
        ...filtered,
        { id: crypto.randomUUID(), fromId: selectedSourceId, toId: blockId }
      ];
    });

    // Reset after creating connection
    setSelectedSourceId(null);
  };

  // Measure the canvas position on screen once (Phase 1 style).
  useLayoutEffect(() => {
    if (ref.current) setCanvasRect(ref.current.getBoundingClientRect());
  }, []);

  // Helper: convert window mouse coords -> canvas coords
  const toCanvasCoords = (clientX: number, clientY: number) => {
    if (!canvasRect) return null;
    return {
      x: clientX - canvasRect.left,
      y: clientY - canvasRect.top
    };
  };

  // ------------- Drop from palette (same as before) -------------
  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const type = e.dataTransfer.getData("application/block-type");
    if (!type || !canvasRect) return;

    const x = e.clientX - canvasRect.left;
    const y = e.clientY - canvasRect.top;

    setBlocks((prev) => [...prev, { id: crypto.randomUUID(), type, x, y, config: {} }]);
  };

  // ------------- Start dragging an existing block -------------
  const handleBlockPointerDown = (blockId: string, e: React.PointerEvent<HTMLDivElement>) => {
    // Only left-click / primary pointer drag
    if (e.button !== 0) return;

    // We must have canvas measured to compute coordinates
    if (!canvasRect) return;

    const canvasPt = toCanvasCoords(e.clientX, e.clientY);
    if (!canvasPt) return;

    // Find the block being dragged (to compute offset)
    const block = blocks.find((b) => b.id === blockId);
    if (!block) return;

    // Store the offset so the block doesn't "snap" its center to the cursor.
    // Without this, the block would jump when you start dragging.
    setDragging({
      blockId,
      offsetX: canvasPt.x - block.x,
      offsetY: canvasPt.y - block.y,
      startClientX: e.clientX,
      startClientY: e.clientY,
      moved: false
    });

    // Capture pointer so we keep receiving events even if cursor leaves block
    (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
  };

  // ------------- Update dragging on pointer move -------------
  const DRAG_THRESHOLD_PX = 3;

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;

    const canvasPt = toCanvasCoords(e.clientX, e.clientY);
    if (!canvasPt) return;

    // Determine if this interaction is truly a drag (moved enough)
    if (!dragging.moved) {
      const dx = e.clientX - dragging.startClientX;
      const dy = e.clientY - dragging.startClientY;
      const dist = Math.hypot(dx, dy);

      if (dist >= DRAG_THRESHOLD_PX) {
        suppressNextClickRef.current = true; // ignore the click that will happen on release
        setDragging((prev) => (prev ? { ...prev, moved: true } : prev));
      }
    }

    const newX = canvasPt.x - dragging.offsetX;
    const newY = canvasPt.y - dragging.offsetY;

    setBlocks((prev) =>
      prev.map((b) => (b.id === dragging.blockId ? { ...b, x: newX, y: newY } : b))
    );
  };

  // ------------- Stop dragging -------------
  const handlePointerUp = () => {
    if (dragging) setDragging(null);
  };

  // ------------- Delete a block -------------
  const handleDelete = (blockId: string) => {
    // Remove the block
    setBlocks((prev) => prev.filter((b) => b.id !== blockId));

    // Remove any connections that touch that block
    setConnections((prev) => prev.filter((c) => c.fromId !== blockId && c.toId !== blockId));

    // If the deleted block was selected as source, clear selection
    setSelectedSourceId((prev) => (prev === blockId ? null : prev));
  };

  const updateBlockConfig = (blockId: string, patch: Record<string, unknown>) => {
    setBlocks((prev) =>
      prev.map((b) =>
        b.id === blockId
          ? { ...b, config: { ...b.config, ...patch } } // merge patch into existing config
          : b
      )
    );
  };

  return (
    <div
      ref={ref}
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      style={{
        flex: 1,
        position: "relative",
        background:
          "repeating-linear-gradient(45deg, #f9fafb, #f9fafb 10px, #f3f4f6 10px, #f3f4f6 20px)",
        overflow: "hidden"
      }}
    >
      <svg
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          // NEW: allow pointer events so hover/click works on paths
          pointerEvents: "auto"
        }}
      >
        {connections.map((c) => {
          const from = blockById.get(c.fromId);
          const to = blockById.get(c.toId);
          if (!from || !to) return null;

          // Start: RIGHT edge of source
          const x1 = from.x + NODE_W / 2;
          const y1 = from.y;

          // End: LEFT edge of receiver
          const x2 = to.x - NODE_W / 2;
          const y2 = to.y;

          // Bend column (middle x). This creates two corners.
          const midX = (x1 + x2) / 2;

          // Orthogonal polyline as a path:
          // (x1,y1) -> (midX,y1) -> (midX,y2) -> (x2,y2)
          const d = `M ${x1} ${y1} L ${midX} ${y1} L ${midX} ${y2} L ${x2} ${y2}`;

          const isHovered = hoveredConnectionId === c.id;

          return (
            <path
              key={c.id}
              d={d}
              fill="none"
              stroke={isHovered ? "red" : "black"}
              strokeWidth={2}
              style={{ cursor: "pointer" }}
              onMouseEnter={() => setHoveredConnectionId(c.id)}
              onMouseLeave={() => setHoveredConnectionId(null)}
              onClick={() => {
                setConnections((prev) => prev.filter((x) => x.id !== c.id));
              }}
            />
          );
        })}
      </svg>

      {blocks.map((block) => {
        const meta = metaByType.get(block.type);
        const label = meta?.label ?? block.type;
        const color = meta?.color ?? "#9ca3af";

        return (
          <BlockNode
            key={block.id}
            block={block}
            label={label}
            color={color}
            onPointerDown={handleBlockPointerDown}
            onDelete={handleDelete}
            onClick={handleBlockClick}
            isSelectedSource={selectedSourceId === block.id}
            onConfigChange={updateBlockConfig}
            runStatus={runStatusById[block.id] ?? "idle"}
          />
        );
      })}
    </div>
  );
}
