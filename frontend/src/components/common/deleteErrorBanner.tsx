import { Link } from "react-router";
import type { ReactNode } from "react";

export interface DeleteErrorDetail {
  code?: string;
  message?: string;
}

export interface DeleteErrorBanner {
  code?: string;
  message?: ReactNode;
}

const IN_FLIGHT_CODES = new Set([
  "version_has_in_flight_jobs",
  "detector_has_in_flight_jobs",
]);

export function detailToDeleteBanner(
  detail: DeleteErrorDetail | undefined,
): DeleteErrorBanner {
  if (!detail) return { message: "Delete failed." };
  if (detail.code && IN_FLIGHT_CODES.has(detail.code)) {
    return {
      code: detail.code,
      message: (
        <>
          {detail.message ?? "Cancel running jobs first."}{" "}
          <Link to="/jobs?status=running" className="underline font-medium">
            See running jobs ↗
          </Link>
        </>
      ),
    };
  }
  return detail;
}
