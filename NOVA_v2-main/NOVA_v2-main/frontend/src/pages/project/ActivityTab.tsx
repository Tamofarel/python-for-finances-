import { api } from "../../api/client";
import type { Project } from "../../api/types";
import { AuditTable } from "../SettingsPage";
import { Notice, Spinner } from "../../components/ui";
import { useLoad } from "../../hooks/useLoad";

export function ActivityTab({ project }: { project: Project }) {
  const { data, error, loading } = useLoad(() => api.projects.activity(project.id), [project.id]);
  if (error) return <Notice>{error}</Notice>;
  if (loading && !data) return <Spinner />;
  return <AuditTable events={data ?? []} empty="No recorded activity for this project yet." />;
}
