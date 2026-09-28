import { useState } from "react"
import type { BlockInstance } from "../lib/types"

const ENRICH_FIELDS = [
	"name",
	"email",
	"phone",
	"company",
	"title",
	"linkedin",
	"website",
	"location",
	"industry",
	"github_url"
] as const

type BlockNodeProps = {
  block: BlockInstance
  label: string
  color: string

  onPointerDown: (blockId: string, e: React.PointerEvent<HTMLDivElement>) => void
  onDelete: (blockId: string) => void

  onClick: (blockId: string) => void
  isSelectedSource: boolean

  onConfigChange: (blockId: string, patch: Record<string, unknown>) => void
  runStatus?: "idle" | "pending" | "running" | "done"
}

const NODE_WIDTH = 160 // <- pick whatever you want; this will be used both collapsed + expanded

export function BlockNode({
  block,
  label,
  color,
  onPointerDown,
  onDelete,
  onClick,
  isSelectedSource,
  onConfigChange,
  runStatus='idle'
}: BlockNodeProps) {
  const [open, setOpen] = useState(false)
  const [fieldsOpen, setFieldsOpen] = useState(false)

  const needsConfig = block.type === "read_csv" || block.type === "filter" || block.type === "enrich_lead"

  const selectedFields: string[] = Array.isArray(block.config?.fields) ? block.config.fields : []
  const availableFields = ENRICH_FIELDS.filter((f) => !selectedFields.includes(f))

  const barColor =
	runStatus === "running" ? "#f59e0b" :
	runStatus === "done" ? "#22c55e" :
	runStatus === "pending" ? "#9ca3af" :
	"transparent"

  const addField = (field: string) => {
    const next = [...selectedFields, field]
    onConfigChange(block.id, { fields: next })
    // collapse dropdown after selection (feels like a real dropdown)
    setFieldsOpen(false)
  }

  const removeField = (field: string) => {
    const next = selectedFields.filter((f) => f !== field)
    onConfigChange(block.id, { fields: next })
  }

  return (
    <div
      onPointerDown={(e) => onPointerDown(block.id, e)}
      onClick={() => onClick(block.id)}
      style={{
        position: "absolute",
        left: block.x,
        top: block.y,
        transform: "translate(-50%, -50%)",
        zIndex: open ? 1000 : 1,

        width: NODE_WIDTH,
        maxWidth: NODE_WIDTH,
        boxSizing: "border-box",

        padding: 10,
        borderRadius: 8,
        background: `${color}22`,
        userSelect: "none",
        border: isSelectedSource ? `3px solid ${color}` : `2px solid ${color}`,
        boxShadow: isSelectedSource
          ? `0 0 0 4px ${color}33, 0 1px 4px rgba(0,0,0,0.08)`
          : "0 1px 4px rgba(0,0,0,0.08)",
        cursor: "grab",

        overflow: open || fieldsOpen ? "visible" : "hidden"
      }}
    >
      <button
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation()
          onDelete(block.id)
        }}
        style={{
          position: "absolute",
          top: 4,
          right: 4,
          width: 16,
          height: 16,
          borderRadius: 999,
          border: "1px solid rgba(0,0,0,0.2)",
          background: "white",
          cursor: "pointer",
          fontSize: 12,
          lineHeight: "14px",
          padding: 0
        }}
      >
        ×
      </button>

      {needsConfig && (
        <button
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation()
            setOpen((v) => !v)
          }}
          style={{
            position: "absolute",
            top: 4,
            left: 4,
            width: 18,
            height: 18,
            borderRadius: 6,
            border: "1px solid rgba(0,0,0,0.2)",
            background: "white",
            cursor: "pointer",
            fontSize: 12,
            lineHeight: "16px",
            padding: 0
          }}
        >
          ⚙︎
        </button>
      )}

      <div
        style={{
          fontSize: 13,
          fontWeight: 700,
          textAlign: "center",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis"
        }}
      >
        {label}
      </div>

      {runStatus !== "idle" && (
        <div
          style={{
            position: "absolute",
            left: 6,
            right: 6,
            bottom: 4,
            height: 3,
            borderRadius: 999,
            background: barColor
          }}
        />
      )}

      {needsConfig && open && (
        <div
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          style={{
            marginTop: 10,
            padding: 8,
            borderRadius: 8,
            background: "rgba(255,255,255,0.9)",
            border: "1px solid rgba(0,0,0,0.12)",

            width: "100%",
            maxWidth: "100%",
            boxSizing: "border-box",
            overflow: "visible"
          }}
        >
          {block.type === "read_csv" && (
            <>
              <div style={{ fontSize: 12, marginBottom: 6 }}>CSV path</div>
              <input
                value={String(block.config?.path ?? "")}
                onChange={(e) => onConfigChange(block.id, { path: e.target.value })}
                placeholder="data/leads.csv"
                style={{
                  width: "100%",
                  maxWidth: "100%",
                  minWidth: 0,
                  display: "block",
                  boxSizing: "border-box",
                  height: 34,
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: "1px solid rgba(0,0,0,0.25)",
                  background: "white",
                  fontSize: 13,
                  lineHeight: "20px",
                  outline: "none"
                }}
              />
            </>
          )}

          {block.type === "filter" && (
            <>
              <div style={{ fontSize: 12, marginBottom: 6 }}>Filter rule</div>
              <input
                value={String(block.config?.rule ?? "")}
                onChange={(e) => onConfigChange(block.id, { rule: e.target.value })}
                placeholder="age > 30"
                style={{
                  width: "100%",
                  maxWidth: "100%",
                  minWidth: 0,
                  display: "block",
                  boxSizing: "border-box",
                  height: 34,
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: "1px solid rgba(0,0,0,0.25)",
                  background: "white",
                  fontSize: 13,
                  lineHeight: "20px",
                  outline: "none"
                }}
              />
            </>
          )}

          {block.type === "enrich_lead" && (
          <>
            <div style={{ fontSize: 12, marginBottom: 6 }}>
              Fields to enrich
            </div>

            {/* Selected chips */}
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 6,
                marginBottom: 8
              }}
            >
              {selectedFields.length === 0 ? (
                <div style={{ fontSize: 12, opacity: 0.7 }}>
                  No fields selected
                </div>
              ) : (
                selectedFields.map((field) => (
                  <div
                    key={field}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "4px 8px",
                      borderRadius: 999,
                      border: "1px solid rgba(0,0,0,0.2)",
                      background: "white",
                      fontSize: 12
                    }}
                  >
                    <span>{field}</span>
                    <button
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation()
                        removeField(field)
                      }}
                      style={{
                        width: 16,
                        height: 16,
                        borderRadius: 999,
                        border: "1px solid rgba(0,0,0,0.15)",
                        background: "rgba(0,0,0,0.04)",
                        cursor: "pointer",
                        fontSize: 12,
                        lineHeight: "14px",
                        padding: 0
                      }}
                      title="Remove"
                      aria-label={`Remove ${field}`}
                    >
                      ×
                    </button>
                  </div>
                ))
              )}
            </div>

            {/* Collapsible dropdown */}
            <div style={{ position: "relative" }}>
              <button
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation()
                  setFieldsOpen((v) => !v)
                }}
                style={{
                  border: "1px solid #ccc",
                  borderRadius: 6,
                  background: "#fff",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  flexWrap: "wrap",
                  width: "100%",
                  boxSizing: "border-box",

                  // 🔽 make it shorter (height)
                  padding: "2px 6px",
                  minHeight: 20,
                  gap: 4,
                  fontSize: 12,
                  lineHeight: "14px",
                }}
              >
                <span style={{ flex: 1, textAlign: "left" }}>
                  {availableFields.length === 0 ? "All fields selected" : "Add field…"}
                </span>

                <span style={{ marginLeft: "auto", opacity: 0.6, flexShrink: 0 }}>
                  {fieldsOpen ? "▲" : "▼"}
                </span>
              </button>

              {fieldsOpen && availableFields.length > 0 && (
                <div
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    top: 38,
                    borderRadius: 8,
                    border: "1px solid rgba(0,0,0,0.2)",
                    background: "white",
                    overflow: "hidden",
                    zIndex: 2000,
                    maxHeight: 160,
                    overflowY: "auto"
                  }}
                >
                  {availableFields.map((field) => (
                    <div
                      key={field}
                      onClick={() => addField(field)}
                      style={{
                        padding: "8px 10px",
                        fontSize: 12,
                        cursor: "pointer",
                        borderTop: "1px solid rgba(0,0,0,0.06)"
                      }}
                    >
                      {field}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}


        </div>
      )}
    </div>
  )
}
