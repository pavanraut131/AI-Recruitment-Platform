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
  status: "queued" | "uploading" | "extracting" | "processing" | "done" | "failed";
  resumeId?: string;
  candidateName?: string;
  score?: number;
  category?: string;
  error?: string;
}

interface Props {
  campaignId: string;
  onProcessingComplete?: () => void; // reserved for future use
}

const statusConfig = {
  queued:     { label: "Queued",      color: "#64748b", Icon: Clock },
  uploading:  { label: "Uploading",   color: "#06b6d4", Icon: Loader2 },
  extracting: { label: "Extracting",  color: "#f59e0b", Icon: Loader2 },
  processing: { label: "AI Analysis", color: "#7c3aed", Icon: Zap },
  done:       { label: "Done",        color: "#10b981", Icon: CheckCircle },
  failed:     { label: "Failed",      color: "#ef4444", Icon: XCircle },
};

const categoryColors: Record<string, string> = {
  strong_match: "#10b981",
  moderate_match: "#06b6d4",
  weak_match: "#f59e0b",
  rejected: "#ef4444",
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function UploadZone({ campaignId, onProcessingComplete: _onProcessingComplete }: Props) {
  const [files, setFiles] = useState<FileItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState("");
  const eventSourceRef = useRef<EventSource | null>(null);

  // SSE subscription — listen for real-time status updates
  useEffect(() => {
    const es = new EventSource(SSE_URL(campaignId));
    eventSourceRef.current = es;

    es.addEventListener("resume_update", (e) => {
      const update: SSEUpdate = JSON.parse(e.data);
      setFiles(prev => prev.map(f => {
        if (f.resumeId === update.resume_id) {
          const updated: FileItem = {
            ...f,
            status: update.status as FileItem["status"],
          };
          if (update.candidate_name) updated.candidateName = update.candidate_name;
          if (update.score !== undefined) updated.score = update.score;
          if (update.category) updated.category = update.category;
          if (update.error) updated.error = update.error;
          return updated;
        }
        return f;
      }));
    });

    es.onerror = () => {
      // SSE will auto-reconnect; suppress console noise
    };

    return () => es.close();
  }, [campaignId]);

  const onDrop = useCallback((accepted: File[]) => {
    setError("");
    const newItems: FileItem[] = accepted.map(f => ({
      file: f,
      status: "queued",
    }));
    setFiles(prev => [...prev, ...newItems]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 50,
    maxSize: 10 * 1024 * 1024,
    onDropRejected: (rejections: { errors: { message: string }[] }[]) => {
      const msgs = rejections.map(r => r.errors.map(e => e.message).join(", ")).join("; ");
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

      // Map resume IDs back to file items
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
    failed: files.filter(f => f.status === "failed").length,
  };

  return (
    <div className="space-y-4">
      {/* Drop zone */}
      <div {...getRootProps()}
        className={clsx("upload-zone p-10 text-center transition-all", isDragActive && "dragging")}>
        <input {...getInputProps()} />
        <div className="flex flex-col items-center gap-3">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: "rgba(124,58,237,0.1)", border: "1px solid rgba(124,58,237,0.2)" }}>
            <Upload size={24} className="text-violet-400" />
          </div>
          <div>
            <p className="text-white font-semibold text-[15px]">
              {isDragActive ? "Drop PDFs here" : "Drag & drop PDF resumes"}
            </p>
            <p className="text-white/40 text-[13px] mt-1">
              or click to browse — up to 50 PDFs, 10MB each
            </p>
          </div>
        </div>
      </div>

      {/* Upload progress */}
      {uploading && (
        <div className="glass-card p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[13px] text-white/60">Uploading to server...</span>
            <span className="text-[13px] font-semibold text-violet-300">{uploadProgress}%</span>
          </div>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${uploadProgress}%` }} />
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 px-4 py-3 rounded-xl text-[13px] text-red-300"
          style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}>
          <AlertCircle size={15} className="mt-0.5 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* File list */}
      {files.length > 0 && (
        <div className="glass-card overflow-hidden">
          {/* Toolbar */}
          <div className="px-5 py-3 border-b border-white/5 flex items-center justify-between">
            <div className="flex items-center gap-4 text-[12px]">
              {counts.queued > 0 && <span className="text-white/40">{counts.queued} queued</span>}
              {counts.processing > 0 && <span className="text-violet-300 pulse-ring">{counts.processing} processing</span>}
              {counts.done > 0 && <span className="text-emerald-400">{counts.done} complete</span>}
              {counts.failed > 0 && <span className="text-red-400">{counts.failed} failed</span>}
            </div>
            <div className="flex items-center gap-2">
              {counts.done > 0 && (
                <button onClick={clearDone} className="text-[11px] text-white/30 hover:text-white/60 transition-colors">
                  Clear done
                </button>
              )}
              {counts.queued > 0 && (
                <button onClick={handleUpload} disabled={uploading} className="btn-primary py-1.5 px-4 text-[13px]">
                  <Upload size={13} />
                  Upload {counts.queued} file{counts.queued > 1 ? "s" : ""}
                </button>
              )}
            </div>
          </div>

          {/* Files */}
          <div className="divide-y divide-white/5 max-h-80 overflow-y-auto">
            {files.map((f, i) => {
              const cfg = statusConfig[f.status];
              const spinning = ["uploading", "extracting", "processing"].includes(f.status);
              return (
                <div key={i} className="flex items-center gap-3 px-5 py-3 hover:bg-white/2 transition-colors fade-in">
                  <FileText size={15} style={{ color: cfg.color, flexShrink: 0 }} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] text-white/80 truncate">{f.file.name}</span>
                      <span className="text-[10px] text-white/30 flex-shrink-0">
                        {(f.file.size / 1024).toFixed(0)}KB
                      </span>
                    </div>
                    {f.status === "done" && f.candidateName && (
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[12px] text-emerald-400">{f.candidateName}</span>
                        {f.score !== undefined && (
                          <span className="text-[11px] px-1.5 py-0.5 rounded font-semibold"
                            style={{
                              background: `${categoryColors[f.category || ""] || "#64748b"}18`,
                              color: categoryColors[f.category || ""] || "#64748b",
                            }}>
                            {f.score.toFixed(0)}/100
                          </span>
                        )}
                      </div>
                    )}
                    {f.status === "failed" && f.error && (
                      <span className="text-[11px] text-red-400">{f.error}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <cfg.Icon
                      size={14}
                      style={{ color: cfg.color }}
                      className={spinning ? "animate-spin" : ""}
                    />
                    <span className="text-[11px] font-medium" style={{ color: cfg.color }}>{cfg.label}</span>
                    {f.status === "queued" && (
                      <button onClick={() => removeFile(i)} className="text-white/20 hover:text-white/60 transition-colors ml-1">
                        <X size={13} />
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
