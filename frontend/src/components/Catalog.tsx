import type { BlockType } from "../lib/types";

type CatalogProps = {
  blockTypes: BlockType[];
};

export function Catalog({ blockTypes }: CatalogProps) {
  return (
    <div style={{ width: 240, borderRight: "1px solid #e5e7eb", padding: 12, boxSizing: "border-box" }}>
      <h3 style={{ marginBottom: 12 }}>Block Catalog</h3>

      {blockTypes.map((bt) => (
        <div
          key={bt.type}
          draggable
          onDragStart={(e) => e.dataTransfer.setData("application/block-type", bt.type)}
          style={{
            padding: 8,
            marginBottom: 8,
            borderRadius: 6,
            border: "1px solid #e5e7eb",
            background: bt.color + "22",
            cursor: "grab",
            fontSize: 14,
          }}
        >
          {bt.label}
        </div>
      ))}
    </div>
  );
}
