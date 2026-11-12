import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { Project } from "../../api/types";
import { Notice, Spinner } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";
import { ActivityTab } from "./ActivityTab";
import { ChangesTab } from "./ChangesTab";
import { DesignTab } from "./DesignTab";
import { FilesTab } from "./FilesTab";
import { IdeasTab } from "./IdeasTab";
import { OverviewTab } from "./OverviewTab";
import { TasksTab } from "./TasksTab";
import { WorkTab } from "./WorkTab";

const TABS = [
  ["overview", "Overview"],
  ["files", "Files"],
  ["changes", "Changes"],
  ["ideas", "Ideas"],
  ["design", "Design"],
  ["work", "Work"],
  ["tasks", "Commands"],
  ["activity", "Activity"],
] as const;

export function ProjectPage() {
  const id = Number(useParams().id);
  const { data, error, setData } = useLoad(() => api.projects.get(id), [id]);
  const [pendingChanges, setPendingChanges] = useState(0);

  if (error) return <div className="page"><Notice>{error}</Notice></div>;
  if (!data) return <div className="page"><Spinner /> Loading project…</div>;
  const project: Project = data;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{project.name}</h1>
          {project.archived && <span className="badge">Archived</span>}
        </div>
      </div>
      <nav className="tabs" aria-label="Project sections">
        {TABS.map(([key, label]) => (
          <NavLink key={key} to={key} className={({ isActive }) => (isActive ? "active" : "")}>
            {label}
            {key === "changes" && pendingChanges > 0 && <span className="count" aria-label={`${pendingChanges} pending`}>{pendingChanges}</span>}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<OverviewTab project={project} onChange={setData} />} />
        <Route path="files" element={<FilesTab project={project} />} />
        <Route path="changes" element={<ChangesTab project={project} onPendingCount={setPendingChanges} />} />
        <Route path="ideas" element={<IdeasTab project={project} />} />
        <Route path="design" element={<DesignTab project={project} />} />
        <Route path="tasks" element={<TasksTab project={project} />} />
        <Route path="work" element={<WorkTab project={project} />} />
        <Route path="activity" element={<ActivityTab project={project} />} />
      </Routes>
    </div>
  );
}
