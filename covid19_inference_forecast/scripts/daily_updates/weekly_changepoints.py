"""
    # Example for weekly changepoints

    Runtime ~ 1h

    ## Importing modules
"""

import datetime
import sys
import pandas as pd
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import scipy.stats
import theano
import theano.tensor as tt
import pymc3 as pm

try:
    import covid19_inference_new as cov19
except ModuleNotFoundError:
    sys.path.append("../..")
    import covid19_inference_new as cov19


""" ## Data retrieval
"""
owd = cov19.data_retrieval.OWD()
owd.download_all_available_data(force_download=True)
data_begin = datetime.datetime.today() - datetime.timedelta(days=31 * 4)
data_end = datetime.datetime.today()
new_cases_obs = owd.get_new(
    "confirmed", country="Germany", data_begin=data_begin, data_end=data_end
)
total_cases_obs = owd.get_total(
    "confirmed", country="Germany", data_begin=data_begin, data_end=data_end
)

""" ## Create weekly changepoints

    TODO:
        Get relative_to_previous working
"""

# Structures change points in a dict. Variables not passed will assume default values.
change_points = [
    dict(
        pr_mean_date_transient=data_begin - datetime.timedelta(days=1),
        pr_sigma_date_transient=1.5,
        pr_median_lambda=0.12,
        pr_sigma_lambda=0.5,
        pr_sigma_transient_len=0.5,
    ),
]
print(f"Adding possible change points at:")
for i, day in enumerate(pd.date_range(start=data_begin, end=datetime.datetime.now())):
    if day.weekday() == 6 and (datetime.datetime.today() - day).days > 9:
        print(f"\t{day}")

        # Prior factor to previous
        change_points.append(
            dict(  # one possible change point every sunday
                pr_mean_date_transient=day,
                pr_sigma_date_transient=1.5,
                pr_sigma_lambda=0.2,  # wiggle compared to previous point
                relative_to_previous=True,
                pr_factor_to_previous=1,
            )
        )

""" ## Put the model together
"""

# Number of days the simulation starts earlier than the data.
# Should be significantly larger than the expected delay in order to always fit the same number of data points.
diff_data_sim = 16
# Number of days in the future (after date_end_data) to forecast cases
num_days_forecast = 10
params_model = dict(
    new_cases_obs=new_cases_obs[:],
    data_begin=data_begin,
    fcast_len=num_days_forecast,
    diff_data_sim=diff_data_sim,
    N_population=83e6,
)
# Median of the prior for the delay in case reporting, we assume 10 days
pr_delay = 10
with cov19.model.Cov19Model(**params_model) as this_model:
    # Create the an array of the time dependent infection rate lambda
    lambda_t_log = cov19.model.lambda_t_with_sigmoids(
        pr_median_lambda_0=0.4,
        pr_sigma_lambda_0=0.5,
        change_points_list=change_points,  # The change point priors we constructed earlier
        name_lambda_t="lambda_t",  # Name for the variable in the trace (see later)
    )

    # set prior distribution for the recovery rate
    mu = pm.Lognormal(name="mu", mu=np.log(1 / 8), sigma=0.2)

    # This builds a decorrelated prior for I_begin for faster inference.
    # It is not necessary to use it, one can simply remove it and use the default argument
    # for pr_I_begin in cov19.SIR
    prior_I = cov19.model.uncorrelated_prior_I(
        lambda_t_log=lambda_t_log,
        mu=mu,
        pr_median_delay=pr_delay,
        name_I_begin="I_begin",
        name_I_begin_ratio_log="I_begin_ratio_log",
        pr_sigma_I_begin=2,
        n_data_points_used=5,
    )

    # Use lambda_t_log and mu to run the SIR model
    new_cases = cov19.model.SIR(
        lambda_t_log=lambda_t_log,
        mu=mu,
        name_new_I_t="new_I_t",
        name_I_t="I_t",
        name_I_begin="I_begin",
        pr_I_begin=prior_I,
    )

    # Delay the cases by a lognormal reporting delay
    new_cases = cov19.model.delay_cases(
        cases=new_cases,
        name_cases="delayed_cases",
        name_delay="delay",
        name_width="delay-width",
        pr_mean_of_median=pr_delay,
        pr_sigma_of_median=0.2,
        pr_median_of_width=0.3,
    )

    # Modulate the inferred cases by a abs(sin(x)) function, to account for weekend effects
    # Also adds the "new_cases" variable to the trace that has all model features.
    new_cases = cov19.model.week_modulation(
        cases=new_cases,
        name_cases="new_cases",
        name_weekend_factor="weekend_factor",
        name_offset_modulation="offset_modulation",
        week_modulation_type="abs_sine",
        pr_mean_weekend_factor=0.3,
        pr_sigma_weekend_factor=0.5,
        weekend_days=(6, 7),
    )

    # Define the likelihood, uses the new_cases_obs set as model parameter
    cov19.model.student_t_likelihood(new_cases)

""" ## MCMC sampling
"""

trace = pm.sample(model=this_model, init="advi", tune=1000, draws=1000)

import pickle

with open(f"./weekly_cp.pickle", "wb") as f:
    pickle.dump((this_model, trace), f)


""" ## Plotting
    
    ### Save path
"""
try:
    # only works when called from python, not reliable in interactive ipython etc.
    os.chdir(os.path.dirname(__file__))
    save_to = "../../figures/weekly_cps_"
except:
    # assume base directory
    save_to = "../../figures/weekly_cps_"

""" ### Timeseries
    Timeseries overview, for now needs an offset variable to get cumulative cases
"""
cov19.plot.rcParams["color_model"] = "tab:orange"
fig, axes = cov19.plot.timeseries_overview(this_model, trace, offset=total_cases_obs[0])

for ax in axes:
    ax.set_xlim(datetime.datetime.now() - datetime.timedelta(days=4 * 17))
# Set y lim for effective growth rate
axes[0].set_ylim(-0.1, 0.2)
axes[1].set_ylim(0, new_cases_obs.max() + 5000)
# Add vline for today

# --------------------------------------------------------------------------- #
# inset new cases
# --------------------------------------------------------------------------- #
# Add inset for march to juli
"""
from mpl_toolkits.axes_grid1.inset_locator import zoomed_inset_axes

axins = axes[1].inset_axes(bounds=[0.1, 0.5, 0.4, 0.4])
for line in axes[1].lines:
    axins.lines.append(line)

ax = axins

new_cases_inset = owd.get_new(
    "confirmed",
    country="Germany",
    data_begin=datetime.datetime(2020, 4, 2),
    data_end=data_end,
)

# model fit
cov19.plot._timeseries(
    x=new_cases_inset.index, y=new_cases_inset, ax=ax, what="model", color="tab:orange",
)
prec = 1.0 / (np.log10(ax.get_ylim()[1]) - 2.5)
if prec < 2.0 and prec >= 0:
    ax.yaxis.set_major_formatter(
        mpl.ticker.FuncFormatter(cov19.plot._format_k(int(prec)))
    )
    ticks = ax.get_xticks()
    ax.set_xticks(ticks=[new_cases_inset.index.min(), new_cases_inset.index.max()])

"""
# save: ts for timeseries
plt.savefig(
    save_to + "ts.pdf", dpi=300, bbox_inches="tight", pad_inches=0.05,
)
plt.savefig(
    save_to + "ts.png", dpi=300, bbox_inches="tight", pad_inches=0.05,
)
""" ### Distributions
"""
num_rows = len(change_points) + 1 + 1
num_columns = int(np.ceil(14 / 5))
fig_width = 4.5 / 3 * num_columns
fig_height = 8 * 1

fig, axes = plt.subplots(
    8, num_columns, figsize=(fig_width, fig_height), constrained_layout=True
)
rows = [
    0,
    num_rows,
    num_rows - 1,
    num_rows - 2,
    num_rows - 3,
    num_rows - 4,
    num_rows - 5,
    num_rows - 6,
]
# Left row we want mu and all lambda_i!
for i in rows:
    if i == 0:
        cov19.plot._distribution(this_model, trace, "mu", axes[0, 0])
    elif i == num_rows:
        # Plot lambda_i and remove the xlable, we add one big label later.
        cov19.plot._distribution(this_model, trace, f"lambda_{0}", axes[1, 0])
        axes[1, 0].set_xlabel("Inital rate")
    else:
        # Plot lambda_i and remove the xlable, we add one big label later.
        cov19.plot._distribution(
            this_model, trace, f"lambda_{i-1}", axes[-i + num_rows + 1, 0]
        )
        axes[-i + num_rows + 1, 0].set_xlabel("")
# middle row
for i in rows:
    if i == 0:
        cov19.plot._distribution(this_model, trace, "sigma_obs", axes[i, 1])
    elif i == num_rows:
        cov19.plot._distribution(this_model, trace, "I_begin", axes[1, 1])
    else:
        # Plot transient_day_i and remove the xlable, we add one big label later.
        cov19.plot._distribution(
            this_model, trace, f"transient_day_{i-1}", axes[-i + num_rows + 1, 1]
        )
        axes[-i + num_rows + 1, 1].set_xlabel("")
# right row
for i in rows:
    if i == 0:
        # Create legend for everything
        axes[i, 2].set_axis_off()
        axes[i, 2].plot(
            [], [], color=cov19.plot.rcParams["color_prior"], linewidth=3, label="Prior"
        )
        axes[i, 2].hist([], color=cov19.plot.rcParams["color_model"], label="Posterior")
        axes[i, 2].legend(loc="center left")
        axes[i, 2].get_legend().get_frame().set_linewidth(0.0)
        axes[i, 2].get_legend().get_frame().set_facecolor("#F0F0F0")
    elif i == num_rows:
        cov19.plot._distribution(this_model, trace, f"delay", axes[1, 2])
        axes[1, 2].set_xlabel("Reporting delay")
    else:
        # Plot transient_len_i and remove the xlable, we add one big label later.
        cov19.plot._distribution(
            this_model, trace, f"transient_len_{i-1}", axes[-i + num_rows + 1, 2]
        )
        axes[-i + num_rows + 1, 2].set_xlabel("")

# Add ylabel for the first axes
axes[0, 0].set_ylabel("Density")
# Set bold xlabel for Spreading rates Change times and Change durations
axes[2, 0].set_xlabel("Spreading rates", fontweight="bold")
axes[2, 1].set_xlabel("Change times", fontweight="bold")
axes[2, 2].set_xlabel("Change duration", fontweight="bold")

# Letters
letter_kwargs = dict(x=-0.3, y=1.1, size="x-large")
axes[0, 0].text(s="D", transform=axes[0, 0].transAxes, **letter_kwargs)
axes[1, 0].text(s="E", transform=axes[1, 0].transAxes, **letter_kwargs)
axes[2, 0].text(s="F", transform=axes[2, 0].transAxes, **letter_kwargs)

# dist for distributions
plt.savefig(
    save_to + "dist.pdf",
    dpi=300,
    bbox_inches="tight",
    pad_inches=0.05,
    transparent=True,
)
plt.savefig(
    save_to + "dist.png",
    dpi=300,
    bbox_inches="tight",
    pad_inches=0.05,
    transparent=True,
)
