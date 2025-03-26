import fppanalysis as fppa
import velocity_estimation as ve
import xarray as xr
import numpy as np
from scipy.optimize import differential_evolution


def run_norm_ds(ds, radius):
    """Returns running normalized dataset of a given dataset using run_norm from
    fppanalysis function by applying xarray apply_ufunc.
    Input:
        - ds: xarray Dataset
        - radius: radius of the window used in run_norm. Window size is 2*radius+1. ... int
    'run_norm' returns a tuple of time base and the signal. Therefore, apply_ufunc will
    return a tuple of two DataArray (corresponding to time base and the signal).
    To return a format like the original dataset, we create a new dataset of normalized frames and
    corresponding time computed from apply_ufunc.
    Description of apply_ufunc arguments.
        - first the function
        - then arguments in the order expected by 'run_norm'
        - input_core_dimensions: list of lists, where the number of inner sequences must match
        the number of input arrays to the function 'run_norm'. Each inner sequence specifies along which
        dimension to align the corresponding input argument. That means, here we want to normalize
        frames along time, hence 'time'.
        - output_core_dimensions: list of lists, where the number of inner sequences must match
        the number of output arrays to the function 'run_norm'.
        - exclude_dims: dimensions allowed to change size. This must be set for some reason.
        - vectorize must be set to True in order to for run_norm to be applied on all pixels.
    """
    import xarray as xr

    normalization = xr.apply_ufunc(
        fppa.run_norm,
        ds["frames"],
        radius,
        ds["time"],
        input_core_dims=[["time"], [], ["time"]],
        output_core_dims=[["time"], ["time"]],
        exclude_dims=set(("time",)),
        vectorize=True,
    )

    ds_normalized = xr.Dataset(
        data_vars=dict(
            frames=(["y", "x", "time"], normalization[0].data),
        ),
        coords=dict(
            time=normalization[1].data[0, 0, :],
        ),
    )

    return ds_normalized


def interpolate_nans_3d(ds, time_dim="time"):
    """
    Replace NaN values in a 3D xarray dataset with linear interpolation
    based on neighboring values.

    Parameters:
        ds (xarray.Dataset or xarray.DataArray): Input dataset or data array.
        spatial_dims (tuple): Names of the two spatial dimensions (default: ('x', 'y')).
        time_dim (str): Name of the time dimension (default: 'time').

    Returns:
        xarray.DataArray: Dataset with NaNs replaced by interpolated values.
    """
    from scipy.interpolate import griddata

    def interpolate_2d(array, x, y):
        """Interpolate a 2D array with NaNs using griddata."""
        valid_mask = ~np.isnan(array)
        if np.sum(valid_mask) < 4:
            return array  # Not enough points to interpolate reliably

        valid_points = np.array((x[valid_mask], y[valid_mask])).T
        valid_values = array[valid_mask]

        nan_points = np.array((x[~valid_mask], y[~valid_mask])).T

        # griddata interpolates only for grid points inside the convex hull, otherwise leave values to nan.
        # For values outside the convex hull we use "nearest", which is good enough for plotting purposes.
        interpolated_values = griddata(
            valid_points, valid_values, nan_points, method="linear"
        )
        interpolated_values_nearest = griddata(
            valid_points, valid_values, nan_points, method="nearest"
        )
        interpolated_values[np.isnan(interpolated_values)] = (
            interpolated_values_nearest[np.isnan(interpolated_values)]
        )
        array[~valid_mask] = interpolated_values

    x, y = np.meshgrid(ds["x"], ds["y"], indexing="xy")

    # Iterate over the time dimension and interpolate each 2D slice
    for t in ds[time_dim].values:
        slice_data = ds["frames"].sel({time_dim: t}).values
        interpolate_2d(slice_data, x, y)


class PhantomDataInterface(ve.ImagingDataInterface):
    """Implementation of ImagingDataInterface for xarray datasets given by the
    code at https://github.com/sajidah-ahmed/cmod_functions."""

    def __init__(self, ds: xr.Dataset):
        self.ds = ds

    def get_shape(self):
        return self.ds.dims["x"], self.ds.dims["y"]

    def get_signal(self, x: int, y: int):
        return self.ds.isel(x=x, y=y)["frames"].values

    def get_dt(self) -> float:
        times = self.ds["time"]
        return float(times[1].values - times[0].values)

    def get_position(self, x: int, y: int):
        return x, y

    def is_pixel_dead(self, x: int, y: int):
        signal = self.get_signal(x, y)
        return len(signal) == 0 or np.isnan(signal[0])


def get_t_start_end(ds):
    times = ds.time.values
    t_start = times[0]
    t_end = times[len(times) - 1]
    return t_start, t_end


def get_dt(ds):
    times = ds["time"]
    return float(times[1].values - times[0].values)


def plot_ccfs_grid(
    ds, ax, refx, refy, rows, cols, delta, ccf=True, plot_tau_M=False, **kwargs
):
    ref_signal = ds["frames"].isel(x=refx, y=refy).values
    tded = ve.TDEDelegator(
        ve.TDEMethod.CC,
        ve.CCOptions(cc_window=delta, minimum_cc_value=0.2, interpolate=True),
        cache=False,
    )

    for row_ix in range(5):
        y = rows[row_ix]
        for col_ix in range(4):
            x = cols[col_ix]
            signal = ds["frames"].isel(x=x, y=y)
            if ccf:
                time, res = fppa.corr_fun(signal, ref_signal, get_dt(ds))
                tau, c, _ = tded.estimate_time_delay(
                    (x, y), (refx, refy), ve.CModImagingDataInterface(ds)
                )
            else:
                Svals, res, s_var, time, peaks, wait = fppa.cond_av(
                    S=signal,
                    T=ds["time"].values,
                    smin=2,
                    Sref=ref_signal,
                    delta=delta * 2,
                )
                tau, c, _ = tded.estimate_time_delay(
                    (x, y), (refx, refy), ve.CModImagingDataInterface(ds)
                )

            window = np.abs(time) < delta
            ax[row_ix, col_ix].plot(time[window], res[window])

            ax[row_ix, col_ix].set_title(
                "R = {:.2f} Z = {:.2f}".format(ds.R.isel(x=x, y=y), ds.Z.isel(x=x, y=y))
            )
            ax[row_ix, col_ix].vlines(0, -10, 10, ls="--")
            ax[row_ix, col_ix].set_ylim(-0.5, 1)
            multiply = 1 if get_dt(ds) > 1e-3 else 1e6
            if tau is not None:
                ax[row_ix, col_ix].text(
                    x=delta / 2, y=0.5, s=r"$\tau = {:.2f}$".format(tau * multiply)
                )
                if plot_tau_M:
                    vx, vy, lx, ly, theta = (
                        kwargs["vx"],
                        kwargs["vy"],
                        kwargs["lx"],
                        kwargs["ly"],
                        kwargs["theta"],
                    )
                    dx, dy = ds.R.isel(x=x, y=y) - ds.R.isel(x=refx, y=refy), ds.Z.isel(
                        x=x, y=y
                    ) - ds.Z.isel(x=refx, y=refy)
                    ax[row_ix, col_ix].text(
                        x=delta / 2,
                        y=0.7,
                        s=r"$\tau_M = {:.2f}$".format(
                            get_taumax(vx, vy, dx, dy, lx, ly, theta) * multiply
                        ),
                    )


def get_ccf_tau(ds):
    s_ref = ds.frames.isel(x=0, y=0).values
    tau, _ = fppa.corr_fun(ds.frames.isel(x=0, y=0).values, s_ref, dt=get_dt(ds))
    return tau


def get_2d_corr(ds, x, y, delta):
    ref_signal = ds.frames.isel(
        x=x, y=y
    ).values  # Select the time series at (refx, refy)

    def corr_wrapper(s):
        tau, res = fppa.corr_fun(
            ref_signal, s, dt=5e-7
        )  # Apply correlation function to each time series
        return res

    ds_corr = xr.apply_ufunc(
        corr_wrapper,
        ds,
        input_core_dims=[
            ["time"]
        ],  # Each function call operates on a single time series
        output_core_dims=[["tau"]],  # Output is also a time array
        vectorize=True,
    )
    tau = get_ccf_tau(ds)
    ds_corr = ds_corr.assign_coords(tau=tau)
    trajectory_times = tau[np.abs(tau) < delta]
    return ds_corr.sel(tau=trajectory_times)


def rotated_blob(params, rx, ry, x, y):
    lx, ly, t = params
    xt = (x - rx) * np.cos(t) + (y - ry) * np.sin(t)
    yt = (y - ry) * np.cos(t) - (x - rx) * np.sin(t)
    return np.exp(-((xt / lx) ** 2) - ((yt / ly) ** 2))


def ellipse_parameters(params, rx, ry, alpha):
    lx, ly, t = params
    xvals = lx * np.cos(alpha) * np.cos(t) - ly * np.sin(alpha) * np.sin(t) + rx
    yvals = lx * np.cos(alpha) * np.sin(t) + ly * np.sin(alpha) * np.cos(t) + ry
    return xvals, yvals


def plot_2d_ccf(ds, x, y, delta, ax):
    corr_data = get_2d_corr(ds, x, y, delta)
    rx, ry = corr_data.R.isel(x=x, y=y).values, corr_data.Z.isel(x=x, y=y).values
    data = corr_data.sel(tau=0).frames.values

    def model(params):
        blob = rotated_blob(params, rx, ry, corr_data.R.values, corr_data.Z.values)
        return np.sum((blob - data) ** 2)

    # Initial guesses for lx, ly, and t
    # Rough estimation
    bounds = [
        (0, 5),  # lx: 0 to 5
        (0, 5),  # ly: 0 to 5
        (-np.pi / 4, np.pi / 4),  # t: 0 to 2π
    ]

    result = differential_evolution(
        model,
        bounds,
        seed=42,  # Optional: for reproducibility
        popsize=15,  # Optional: population size multiplier
        maxiter=1000,  # Optional: maximum number of iterations
    )

    if ax is not None:
        im = ax.imshow(
            corr_data.sel(tau=0).frames, origin="lower", interpolation="spline16"
        )
        ax.scatter(rx, ry, color="black")
        rmin, rmax, zmin, zmax = (
            corr_data.R[0, 0] - 0.05,
            corr_data.R[0, -1] + 0.05,
            corr_data.Z[0, 0] - 0.05,
            corr_data.Z[-1, 0] + 0.05,
        )

        alphas = np.linspace(0, 2 * np.pi, 200)
        elipsx, elipsy = zip(*[ellipse_parameters(result.x, rx, ry, a) for a in alphas])
        ax.plot(elipsx, elipsy)
        im.set_extent((rmin, rmax, zmin, zmax))

    return result.x


def get_taumax(v, w, dx, dy, lx, ly, t):
    lx_fit, ly_fit = lx, ly
    t_fit = t
    a1 = (dx * ly_fit**2 * v + dy * lx_fit**2 * w) * np.cos(t_fit) ** 2
    a2 = (lx_fit**2 - ly_fit**2) * (dy * v + dx * w) * np.cos(t_fit) * np.sin(t_fit)
    a3 = (dx * lx_fit**2 * v + dy * ly_fit**2 * w) * np.sin(t_fit) ** 2
    d1 = (ly_fit**2 * v**2 + lx_fit**2 * w**2) * np.cos(t_fit) ** 2
    d2 = (lx_fit**2 * v**2 + ly_fit**2 * w**2) * np.sin(t_fit) ** 2
    d3 = 2 * (lx_fit**2 - ly_fit**2) * v * w * np.cos(t_fit) * np.sin(t_fit)
    return (a1 - a2 + a3) / (d1 + d2 - d3)


def find_events(ds, refx, refy, threshold=3, window_size=10, check_max=0):
    """
    Find events where reference pixel exceeds threshold and extract windows around peaks.

    Parameters:
    ds (xarray.Dataset): Input dataset with time, x, y coordinates
    refx (int): X index of reference pixel
    refy (int): Y index of reference pixel
    threshold (float): Threshold value for event detection
    window_size (int): Size of window to extract around peaks

    Returns:
    list: List of xarray.Dataset objects containing extracted windows
    """
    # Assuming the data variable is named 'data' - adjust if different
    ref_ts = ds.frames.isel(x=refx, y=refy)

    # Find indices where signal exceeds threshold
    above_threshold = ref_ts > threshold
    indices = np.where(above_threshold)[0]

    # Split into contiguous events
    events = []
    if len(indices) > 0:
        diffs = np.diff(indices)
        split_points = np.where(diffs > 1)[0] + 1
        events = np.split(indices, split_points)

    print("Found {} events".format(len(events)))
    windows = []
    half_window = window_size // 2
    discarded_events_zero_len = 0
    discarded_events_not_max = 0
    discarded_events_truncated = 0

    for event in events:
        if len(event) == 0:
            discarded_events_zero_len += 1
            continue

        # Find peak within the event
        event_ts = ref_ts.isel(time=event)
        max_idx_in_event = event_ts.argmax().item()
        peak_time_idx = event[max_idx_in_event]

        if check_max != 0:
            ref_peak = ds.frames.isel(time=peak_time_idx, x=refx, y=refy).item()
            fromx = max(refx - check_max, 0)
            tox = min(refx + check_max, ds.sizes["x"] - 1)
            fromy = max(refy - check_max, 0)
            toy = min(refy + check_max, ds.sizes["y"] - 1)
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

        # Extract window for all pixels
        window = ds.isel(time=slice(start, end))
        windows.append(window)

    print(
        "Discarded {} events. Not max {}, zero len {}, truncation {}".format(
            discarded_events_not_max
            + discarded_events_zero_len
            + discarded_events_truncated,
            discarded_events_not_max,
            discarded_events_zero_len,
            discarded_events_truncated,
        )
    )
    return windows


def compute_average_event(windows):
    """
    Compute average event across all windows by aligning peak times.

    Parameters:
    windows (list of xarray.Dataset): List of event windows from find_events_and_extract_windows

    Returns:
    xarray.Dataset: Dataset containing average event across all input events
    """
    processed = []
    for win in windows:
        # Create relative time coordinates centered on peak
        time_length = win.sizes["time"]
        half_window = (time_length - 1) // 2
        relative_time = np.arange(time_length) - half_window

        # Assign new time coordinates
        win = win.assign_coords(time=relative_time)
        processed.append(win)

    # Combine all events along new dimension and compute mean
    return xr.concat(processed, dim="event").mean(dim="event")


def plot_average_blob(average, refx, refy, ax):
    rx, ry = average.R.isel(x=refx, y=refy).item(), average.Z.isel(x=refx, y=refy).item()
    R_min, R_max = average.R.min().item(), average.R.max().item()
    Z_min, Z_max = average.Z.min().item(), average.Z.max().item()

    average_blob = average.sel(time=0).frames.values
    average_blob = average_blob/np.max(average_blob)

    def model(params):
        """Objective function with regularization"""
        lx, ly, t = params
        blob = rotated_blob(params, rx, ry, average.R.values, average.Z.values)
        diff = blob - average_blob

        # Add regularization to prevent lx/ly from collapsing
        reg = 0.01 * (1 / lx**2 + 1 / ly**2)
        return np.sum(diff**2) + reg


    # Initial guesses for lx, ly, and t
    # Rough estimation
    bounds = [
        (0, 5),  # lx: 0 to 5
        (0, 5),  # ly: 0 to 5
        (-np.pi / 4, np.pi / 4),  # t: 0 to 2π
    ]

    result = differential_evolution(
        model,
        bounds,
        seed=42,  # Optional: for reproducibility
        popsize=150,  # Optional: population size multiplier
        maxiter=1000,  # Optional: maximum number of iterations
    )

    alphas = np.linspace(0, 2 * np.pi, 200)
    elipsx, elipsy = zip(*[ellipse_parameters(result.x, rx, ry, a) for a in alphas])
    ax.plot(elipsx, elipsy)

    im = ax.imshow(
        average_blob,
        origin="lower",
        interpolation="spline16",
        extent=(R_min, R_max, Z_min, Z_max),
    )
    ax.scatter(rx, ry)

    return result.x
