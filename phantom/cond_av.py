import numpy as np
import xarray as xr


def find_events_and_2dca(
    ds,
    refx,
    refy,
    threshold=3,
    window_size=60,
    check_max=0,
    single_counting=False,
    verbose=True,
):
    """
    Find events where reference pixel exceeds threshold and extract windows around peaks.

    Parameters:
    ds (xarray.Dataset): Input dataset with time, x, y coordinates
    refx (int): X index of reference pixel
    refy (int): Y index of reference pixel
    threshold (float): Threshold value for event detection
    window_size (int): Size of window to extract around peaks
    check_max (int): Radius of the area on which the reference pixel is checked to be maximum at peak time
    single_counting (bool): If True, ensures a minimum distance between events given by window_size.
    verbose (bool): If True, print some method information.

    Returns:
    events: List of xarray.Dataset objects containing extracted windows
    average: xarray.Dataset containing average event across all input events
    """
    # Assuming the data is on cmod_functions format, in xarray and with frames name
    ref_ts = ds.frames.isel(x=refx, y=refy)

    above_threshold = ref_ts > threshold
    indices = np.where(above_threshold)[0]

    events = []
    if len(indices) > 0:
        diffs = np.diff(indices)
        split_points = np.where(diffs > 1)[0] + 1
        events = np.split(indices, split_points)

    if verbose:
        print("Found {} events".format(len(events)))
    candidate_events = []
    half_window = window_size // 2
    discarded_events_zero_len = 0
    discarded_events_not_max = 0
    discarded_events_truncated = 0
    discarded_events_single_count = 0

    for event in events:
        if len(event) == 0:
            discarded_events_zero_len += 1
            continue

        event_ts = ref_ts.isel(time=event)
        max_idx_in_event = event_ts.argmax().item()
        peak_time_idx = event[max_idx_in_event]

        if check_max != 0:
            ref_peak = ds.frames.isel(time=peak_time_idx, x=refx, y=refy).item()
            fromx = max(refx - check_max, 0)
            tox = min(refx + check_max, ds.sizes["x"])
            fromy = max(refy - check_max, 0)
            toy = min(refy + check_max, ds.sizes["y"])
            global_peak = (
                ds.frames.isel(
                    time=peak_time_idx, x=slice(fromx, tox), y=slice(fromy, toy)
                )
                .max()
                .item()
            )
            if not np.isclose(ref_peak, global_peak, atol=1e-6):
                discarded_events_not_max += 1
                continue

        # Calculate window bounds
        start = max(0, peak_time_idx - half_window)
        end = min(len(ds.time), peak_time_idx + half_window + 1)  # +1 for inclusive end

        # Skip incomplete windows if needed (optional)
        if (end - start) < window_size:
            discarded_events_truncated += 1
            continue

        # Store candidate event
        candidate_events.append(
            {
                "peak_time": peak_time_idx,
                "peak_value": ref_ts.isel(time=peak_time_idx).item(),
                "start": start,
                "end": end,
            }
        )

    if single_counting:
        # Sort candidates by peak value descending
        candidate_events.sort(key=lambda x: -x["peak_value"])
        selected_events = []

        for candidate in candidate_events:
            conflict = False
            # Check against already selected events
            for selected in selected_events:
                if abs(candidate["peak_time"] - selected["peak_time"]) < window_size:
                    conflict = True
                    discarded_events_single_count += 1
                    break
            if not conflict:
                selected_events.append(candidate)

        candidate_events = selected_events

    windows = []
    for candidate in candidate_events:
        window = ds.isel(time=slice(candidate["start"], candidate["end"]))
        windows.append(window)

    if verbose:
        print(
            "Discarded {} events. Not max {}, zero len {}, truncation {}, single count {}".format(
                discarded_events_not_max
                + discarded_events_zero_len
                + discarded_events_truncated
                + discarded_events_single_count,
                discarded_events_not_max,
                discarded_events_zero_len,
                discarded_events_truncated,
                discarded_events_single_count,
            )
        )

    # Processed events are the same as events in windows but with a time base relative to their maximum,
    # to make averaging possible
    processed = []
    event_id = 0
    for win in windows:
        # Create relative time coordinates centered on peak
        time_length = win.sizes["time"]
        half_window = (time_length - 1) // 2
        relative_time = np.arange(time_length) - half_window
        abs_time = win.time[half_window].item()

        # Assign new time coordinates
        dt = float(ds["time"][1].values - ds["time"][0].values)
        win = win.assign_coords(time=relative_time * dt)
        win["event_id"] = event_id
        win["refx"] = refx
        win["refy"] = refy
        win["abs_time"] = abs_time
        event_id += 1
        processed.append(win)

    # Combine all events along new dimension and compute mean
    if len(processed) != 0:
        conditional_average = xr.concat(processed, dim="event").mean(dim="event").frames
        mean2 = (
            xr.apply_ufunc(lambda x: x**2, xr.concat(processed, dim="event"))
            .mean(dim="event")
            .frames
        )
        cond_av_ds = xr.Dataset(
            {
                "cond_av": conditional_average,
                "cond_repr": conditional_average**2 / mean2,
            }
        )
        cond_av_ds["refx"] = refx
        cond_av_ds["refy"] = refy
        cond_av_ds["number_events"] = len(processed)
        return processed, cond_av_ds

    return processed, None
