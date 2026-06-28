"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import {
  Upload, FileText, CheckCircle, XCircle, Clock, Loader2,
  AlertCircle, Zap, X
} from "lucide-react";
import clsx from "clsx";
import { uploadResumes, SSE_URL, SSEUpdate } from "@/lib/api";

interface FileItem {
  file: File;
  status: "queued" | "uploading" | "extracting" | "processing" | "done" | "failed" | "quota_exceeded";
  resumeId?: string;
  candidateName?: string;
  score?: number;
  category?: string;
  error?: string;
}

interface Props {
  campaignId: string;
  onProcessingComplete?: () => void;
}

const statusConfig = {
  queued:         { label: "Queued",        color: "rgba(245,240,232,0.35)", Icon: Clock },
  uploading:      { label: "Uploading",     color: "#6366f1",                Icon: Loader2 },
  extracting:     { label: "Extracting",    color: "#f59e0b",                Icon: Loader2 },
  processing:     { label: "AI Analysis",   color: "#d97706",                Icon: Zap },
  done:           { label: "Done",          color: "#22c55e",                Icon: CheckCircle },
  failed:         { label: "Failed",        color: "#ef4444",                Icon: XCircle },
  quota_exceeded: { label: "Quota Limit",   color: "#f97316",                Icon: AlertCircle },
};

const categoryColors: Record<string, string> = {
  strong_match:   "#22c55e",
  moderate_match: "#6366f1",
  weak_match:     "#f59e0b",
  rejected:       "#ef4444",
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function UploadZone({ campaignId, onProcessingComplete: _onProcessingComplete }: Props) {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState("");
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(SSE_URL(campaignId));
    eventSourceRef.current = es;

    es.addEventListener("resume_update", (e) => {
      const update: SSEUpdate = JSON.parse(e.data);
      setFiles(prev => prev.map(f => {
        if (f.resumeId === update.resume_id) {
          const updated: FileItem = { ...f, status: update.status as FileItem["status"] };
          if (update.candidate_name) updated.candidateName = update.candidate_name;
          if (update.score !== undefined) updated.score = update.score;
          if (update.category) updated.category = update.category;
          if (update.error) updated.error = update.error;
          return updated;
        }
        return f;
      }));
    });

    es.onerror = () => {};
    return () => es.close();
  }, [campaignId]);

  const onDrop = useCallback((accepted: File[]) => {
    setError("");
    const newItems: FileItem[] = accepted.map(f => ({ file: f, status: "queued" }));
    setFiles(prev => [...prev, ...newItems]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 50,
    maxSize: 10 * 1024 * 1024,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onDropRejected: (rejections: any[]) => {
      const msgs = rejections.map((r: any) => r.errors.map((e: any) => e.message).join(", ")).join("; ");
      setError(`Some files rejected: ${msgs}`);
    },
  });

  const handleUpload = async () => {
    const queued = files.filter(f => f.status === "queued");
    if (!queued.length) return;

    setUploading(true);
    setError("");
    setFiles(prev => prev.map(f => f.status === "queued" ? { ...f, status: "uploading" } : f));

    try {
      const result = await uploadResumes(
        campaignId,
        queued.map(f => f.file),
        setUploadProgress,
      );

      setFiles(prev => {
        const updated = [...prev];
        let idx = 0;
        for (let i = 0; i < updated.length; i++) {
          if (updated[i].status === "uploading") {
            updated[i] = {
              ...updated[i],
              status: "extracting",
              resumeId: result.resume_ids[idx]?.toString(),
            };
            idx++;
          }
        }
        return updated;
      });

      if (result.rejected_files?.length) {
        setError(`${result.rejected_files.length} file(s) rejected by server.`);
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || "Upload failed. Please try again.");
      setFiles(prev => prev.map(f => f.status === "uploading" ? { ...f, status: "failed" } : f));
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const clearDone = () => setFiles(prev => prev.filter(f => f.status !== "done"));
  const removeFile = (idx: number) => setFiles(prev => prev.filter((_, i) => i !== idx));

  const counts = {
    queued: files.filter(f => f.status === "queued").length,
    processing: files.filter(f => ["uploading", "extracting", "processing"].includes(f.status)).length,
    done: files.filter(f => f.status === "done").length,
    failed: files.filter(f => f.status === "failed" || f.status === "quota_exceeded").length,
  };

  return (
    <div className="space-y-4">
      {/* ── Drop zone ─────────────────────────────────── */}
      <div
        {...getRootProps()}
        className={clsx("upload-zone p-12 text-center", isDragActive && "dragging")}
      >
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-4">
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center"
            style={{
              background: isDragActive ? "rgba(217,119,6,0.12)" : "rgba(255,248,235,0.04)",
              border: `1px solid ${isDragActive ? "rgba(217,119,6,0.35)" : "rgba(255,248,235,0.08)"}`,
              transition: "all 0.2s",
            }}
          >
            <Upload
              size={20}
              strokeWidth={1.75}
              style={{ color: isDragActive ? "#d97706" : "rgba(245,240,232,0.5)" }}
            />
          </div>
          <div>
            <p className="text-[14px] font-semibold text-[#f5f0e8] tracking-tight">
              {isDragActive ? "Release to add files" : "Drop PDF resumes here"}
            </p>
            <p className="text-[12px] mt-1" style={{ color: "rgba(245,240,232,0.35)" }}>
              or click to browse — up to 50 PDFs, 10 MB each
            </p>
          </div>
        </div>
      </div>

      {/* ── Upload progress ────────────────────────────── */}
      {uploading && (
        <div
          className="p-4 rounded-xl fade-in"
          style={{ background: "rgba(255,248,235,0.025)", border: "1px solid rgba(255,248,235,0.07)" }}
        >
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-[12.5px]" style={{ color: "rgba(245,240,232,0.55)" }}>
              Uploading to server...
            </span>
            <span
              className="text-[12px] font-semibold tabular-nums"
              style={{ color: "#d97706", fontFamily: "var(--font-mono)" }}
            >
              {uploadProgress}%
            </span>
          </div>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${uploadProgress}%` }} />
          </div>
        </div>
      )}

      {/* ── Error ─────────────────────────────────────── */}
      {error && (
        <div
          className="flex items-start gap-2.5 px-4 py-3 rounded-xl text-[12.5px] fade-in"
          style={{ background: "rgba(239,68,68,0.07)", border: "1px solid rgba(239,68,68,0.18)", color: "#f87171" }}
        >
          <AlertCircle size={14} strokeWidth={2} style={{ marginTop: "1px", flexShrink: 0 }} />
          {error}
        </div>
      )}

      {/* ── File list ─────────────────────────────────── */}
      {files.length > 0 && (
        <div
          className="rounded-xl overflow-hidden"
          style={{ border: "1px solid rgba(255,248,235,0.07)", background: "#131210" }}
        >
          {/* Toolbar */}
          <div
            className="px-5 py-3 flex items-center justify-between"
            style={{ borderBottom: "1px solid rgba(255,248,235,0.05)" }}
          >
            <div className="flex items-center gap-4 text-[11.5px]">
              {counts.queued > 0 && (
                <span style={{ color: "rgba(245,240,232,0.38)" }}>{counts.queued} queued</span>
              )}
              {counts.processing > 0 && (
                <span style={{ color: "#d97706" }} className="pulse-ring">
                  {counts.processing} processing
                </span>
              )}
              {counts.done > 0 && (
                <span style={{ color: "#22c55e" }}>{counts.done} complete</span>
              )}
              {counts.failed > 0 && (
                <span style={{ color: "#ef4444" }}>{counts.failed} failed</span>
              )}
            </div>
            <div className="flex items-center gap-2.5">
              {counts.done > 0 && (
                <button
                  onClick={clearDone}
                  className="text-[11px] font-medium transition-colors"
                  style={{ color: "rgba(245,240,232,0.28)" }}
                  onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.55)"}
                  onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.28)"}
                >
                  Clear done
                </button>
              )}
              {counts.queued > 0 && (
                <button
                  onClick={handleUpload}
                  disabled={uploading}
                  className="btn-primary"
                  style={{ padding: "6px 14px", fontSize: "12px" }}
                >
                  <Upload size={12} strokeWidth={2.5} />
                  Upload {counts.queued} file{counts.queued > 1 ? "s" : ""}
                </button>
              )}
            </div>
          </div>

          {/* Files */}
          <div className="divide-y max-h-80 overflow-y-auto">
            {files.map((f, i) => {
              const cfg = statusConfig[f.status];
              const spinning = ["uploading", "extracting", "processing"].includes(f.status);
              return (
                <div
                  key={i}
                  className="flex items-center gap-3 px-5 py-3 fade-in table-row-hover"
                  style={{ borderColor: "rgba(255,248,235,0.04)" }}
                >
                  <FileText size={14} style={{ color: cfg.color, flexShrink: 0 }} strokeWidth={1.75} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] text-[#f5f0e8] truncate font-medium">{f.file.name}</span>
                      <span
                        className="text-[10px] flex-shrink-0"
                        style={{ color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)" }}
                      >
                        {(f.file.size / 1024).toFixed(0)}KB
                      </span>
                    </div>
                    {f.status === "done" && f.candidateName && (
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[11.5px] font-medium" style={{ color: "#22c55e" }}>
                          {f.candidateName}
                        </span>
                        {f.score !== undefined && (
                          <span
                            className="text-[10.5px] px-1.5 py-0.5 rounded font-semibold"
                            style={{
                              background: `${categoryColors[f.category || ""] || "#64748b"}14`,
                              color: categoryColors[f.category || ""] || "#64748b",
                              border: `1px solid ${categoryColors[f.category || ""] || "#64748b"}22`,
                            }}
                          >
                            {f.score.toFixed(0)}/100
                          </span>
                        )}
                      </div>
                    )}
                    {(f.status === "failed" || f.status === "quota_exceeded") && f.error && (
                      <span className="text-[11px] mt-0.5" style={{ color: "#f87171" }}>{f.error}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <cfg.Icon
                      size={13}
                      style={{ color: cfg.color }}
                      strokeWidth={2}
                      className={spinning ? "animate-spin" : ""}
                    />
                    <span className="text-[11px] font-medium" style={{ color: cfg.color }}>
                      {cfg.label}
                    </span>
                    {f.status === "queued" && (
                      <button
                        onClick={() => removeFile(i)}
                        className="transition-colors ml-1"
                        style={{ color: "rgba(245,240,232,0.2)" }}
                        onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.55)"}
                        onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.2)"}
                      >
                        <X size={12} strokeWidth={2.5} />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
