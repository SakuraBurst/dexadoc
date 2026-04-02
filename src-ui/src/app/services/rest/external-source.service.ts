import { HttpClient } from '@angular/common/http'
import { Injectable } from '@angular/core'
import { Observable } from 'rxjs'
import { environment } from 'src/environments/environment'

export interface ExternalSource {
  id?: number
  code?: string
  name?: string
  mount_root?: string
  display_root?: string
  enabled?: boolean
  recursive?: boolean
  follow_symlinks?: boolean
  include_regex?: string
  exclude_regex?: string
  max_depth?: number
  max_file_size_mb?: number
  scan_interval_minutes?: number
  last_scan_started_at?: string
  last_scan_finished_at?: string
  last_scan_status?: string
  last_scan_message?: string
}

@Injectable({
  providedIn: 'root',
})
export class ExternalSourceService {
  private baseUrl = `${environment.apiBaseUrl}external_sources/`

  constructor(private http: HttpClient) {}

  list(): Observable<{ results: ExternalSource[] }> {
    return this.http.get<{ results: ExternalSource[] }>(this.baseUrl)
  }

  get(id: number): Observable<ExternalSource> {
    return this.http.get<ExternalSource>(`${this.baseUrl}${id}/`)
  }

  scan(id: number, mode: string = 'delta'): Observable<any> {
    return this.http.post(`${this.baseUrl}${id}/scan/`, { mode })
  }
}
