import logging
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.urlresolvers import reverse
from django.http import (HttpResponse, HttpResponseForbidden,
                         HttpResponsePermanentRedirect)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.context import Context
from django.utils import simplejson as json
from django.views.generic import (CreateView, DeleteView, DetailView,
                                  ListView, TemplateView, UpdateView, View)

from scrum.forms import (CreateProjectForm, CreateTeamForm, BZURLForm,
                         ProjectBugsForm, ProjectForm, SprintBugsForm,
                         SprintForm, TeamForm)
from scrum.models import BugzillaURL, BZError, Project, Sprint, Team, Bug
from scrum.utils import get_blocked_bugs


log = logging.getLogger(__name__)


class ProtectedCreateView(CreateView):
    def dispatch(self, request, *args, **kwargs):
        @permission_required('%s.add_%s' % (self.model._meta.app_label,
                                            self.model._meta.module_name))
        def wrap(request, *args, **kwargs):
            return super(ProtectedCreateView, self).dispatch(request,
                                                             *args, **kwargs)
        return wrap(request, *args, **kwargs)


class ProtectedUpdateView(UpdateView):
    def dispatch(self, request, *args, **kwargs):
        @permission_required('%s.change_%s' % (self.model._meta.app_label,
                                               self.model._meta.module_name))
        def wrap(request, *args, **kwargs):
            return super(ProtectedUpdateView, self).dispatch(request,
                                                             *args, **kwargs)
        return wrap(request, *args, **kwargs)


class ProtectedDeleteView(DeleteView):
    def dispatch(self, request, *args, **kwargs):
        @permission_required('%s.delete_%s' % (self.model._meta.app_label,
                                               self.model._meta.module_name))
        def wrap(request, *args, **kwargs):
            return super(ProtectedDeleteView, self).dispatch(request,
                                                             *args, **kwargs)
        return wrap(request, *args, **kwargs)


class BugsDataMixin(object):
    def get_context_data(self, **kwargs):
        context = super(BugsDataMixin, self).get_context_data(**kwargs)
        self.bugs_kwargs = {}
        # clear cache if requested
        if self.request.META.get('HTTP_CACHE_CONTROL') == 'no-cache':
            self.bugs_kwargs['refresh'] = True
            messages.info(self.request, "The bugs will be refreshed from "
                          "Bugzilla in 10 minutes or so.")
        if 'all' in self.request.GET:
            self.bugs_kwargs['scrum_only'] = False
        try:
            context['bz_search_url'] = self.object.get_bz_search_url().url
        except AttributeError:
            pass
        context['scrum_only'] = self.bugs_kwargs.get('scrum_only', True)
        context['refresh'] = self.bugs_kwargs.get('refresh', False)
        try:
            bugs = self.object.get_bugs(**self.bugs_kwargs)
            context['blocked_bugs'] = get_blocked_bugs(bugs)
            context['bugs'] = bugs
            context['bugs_data'] = self.object.get_graph_bug_data()
            context['bugs_data_json'] = json.dumps(context['bugs_data'])
            context['bzerror'] = False
        except BZError:
            context['bzerror'] = True
        return context


class ProjectsMixin(object):
    model = Project

    def get_context_data(self, **kwargs):
        context = super(ProjectsMixin, self).get_context_data(**kwargs)
        context['projects'] = Project.objects.all()
        if hasattr(self, 'project'):
            context['project'] = self.project
        return context


class HomeView(TemplateView):
    template_name = 'scrum/home.html'
home = HomeView.as_view()


class ProjectView(BugsDataMixin, ProjectsMixin, DetailView):
    template_name = 'scrum/project.html'

    def get_context_data(self, **kwargs):
        context = super(ProjectView, self).get_context_data(**kwargs)
        today = date.today()
        bugs = Bug.objects.filter(sprint__start_date__lte=today,
                                  sprint__end_date__gte=today,
                                  project=self.object)
        context['sprinting'] = bugs
        context['sprinting_blocked'] = get_blocked_bugs(bugs)
        return context


class TeamView(DetailView):
    model = Team
    template_name = 'scrum/team.html'


class ListTeamsView(ListView):
    model = Team
    template_name = 'scrum/teams_list.html'
    context_object_name = 'teams'


class CreateTeamView(ProtectedCreateView):
    model = Team
    form_class = CreateTeamForm
    template_name = 'scrum/team_form.html'


class ListProjectsView(ProjectsMixin, ListView):
    template_name = 'scrum/projects_list.html'
    context_object_name = 'projects'


class CreateProjectView(ProjectsMixin, ProtectedCreateView):
    model = Project
    form_class = CreateProjectForm
    template_name = 'scrum/project_form.html'


class EditProjectView(ProjectsMixin, ProtectedUpdateView):
    form_class = ProjectForm
    template_name = 'scrum/project_form.html'


class CreateSprintView(ProtectedCreateView):
    model = Sprint
    form_class = SprintForm
    template_name = 'scrum/sprint_form.html'

    def get_context_data(self, **kwargs):
        context = super(CreateSprintView, self).get_context_data(**kwargs)
        context['team'] = self.team
        return context

    def get_success_url(self):
        return reverse('scrum_sprint_bugs', args=[self.team.slug,
                                                  self.object.slug])

    def get_form_kwargs(self):
        kwargs = super(CreateSprintView, self).get_form_kwargs()
        if 'data' in kwargs:
            print kwargs['data']
            form_data = kwargs['data'].copy()
            form_data['team'] = self.team.id
            kwargs['data'] = form_data
        return kwargs

    def get_initial(self):
        self.team = get_object_or_404(Team, slug=self.kwargs['slug'])
        return super(CreateSprintView, self).get_initial()

    def form_valid(self, form):
        return super(CreateSprintView, self).form_valid(form)


class SprintMixin(object):
    model = Sprint
    context_object_name = 'sprint'

    def get_object(self, queryset=None):
        tslug = self.kwargs.get('slug')
        sslug = self.kwargs.get('sslug')
        sprint = get_object_or_404(Sprint, team__slug=tslug, slug=sslug)
        self.team = sprint.team
        return sprint

    def get_context_data(self, **kwargs):
        context = super(SprintMixin, self).get_context_data(**kwargs)
        context['team'] = self.team
        return context


class SprintView(BugsDataMixin, SprintMixin, DetailView):
    template_name = 'scrum/sprint.html'

    def get_context_data(self, **kwargs):
        context = super(SprintView, self).get_context_data(**kwargs)
        context['team'] = self.team
        if not context['bzerror']:
            context['bugs_data'].update(self.object.get_burndown_data())
            context['bugs_data_json'] = json.dumps(context['bugs_data'])
        return context


class EditSprintView(SprintMixin, ProjectsMixin, ProtectedUpdateView):
    form_class = SprintForm
    template_name = 'scrum/sprint_form.html'


class EditTeamView(ProtectedUpdateView):
    model = Team
    form_class = TeamForm
    template_name = 'scrum/team_form.html'


class ManageSprintBugsView(BugsDataMixin, SprintMixin, ProtectedUpdateView):
    form_class = SprintBugsForm
    template_name = 'scrum/sprint_bugs.html'

    def get_context_data(self, **kwargs):
        context = super(ManageSprintBugsView, self).get_context_data(**kwargs)
        if not context['bzerror']:
            try:
                bugs = self.team.get_bugs(**self.bugs_kwargs)
                context['backlog_bugs'] = bugs
                context['blocked_backlog_bugs'] = get_blocked_bugs(bugs)
                context['bugs_data'].update(self.object.get_burndown_data())
                context['bugs_data_json'] = json.dumps(context['bugs_data'])
            except BZError:
                context['bzerror'] = True
        return context


class ManageProjectBugsView(BugsDataMixin, ProtectedUpdateView):
    model = Project
    form_class = ProjectBugsForm
    context_object_name = 'project'
    template_name = 'scrum/project_bugs.html'

    def get_context_data(self, **kwargs):
        context = super(ManageProjectBugsView, self).get_context_data(**kwargs)
        if not context['bzerror']:
            try:
                bugs = self.object.get_backlog(**self.bugs_kwargs)
                context['backlog_bugs'] = bugs
                context['blocked_backlog_bugs'] = get_blocked_bugs(bugs)
            except BZError:
                context['bzerror'] = True
        return context


class CreateBZUrlView(ProtectedCreateView):
    model = BugzillaURL
    form_class = BZURLForm
    template_name = 'scrum/bzurl_list.html'

    def get_context_data(self, **kwargs):
        obj = get_object_or_404(Project, slug=self.kwargs['slug'])
        kwargs['target_obj'] = obj
        kwargs['bzurls'] = obj.urls.all()
        return super(CreateBZUrlView, self).get_context_data(**kwargs)

    def form_valid(self, form):
        url = form.save(commit=False)
        url.project = get_object_or_404(Project, slug=self.kwargs['slug'])
        url.save()
        if self.request.is_ajax():
            return self.render_to_response(self.get_context_data(form=form))
        else:
            return redirect(self.target_obj.get_edit_url())

    def form_invalid(self, form):
        if self.request.is_ajax():
            return HttpResponse(json.dumps(form.errors), status=400)
        else:
            target_obj = get_object_or_404(Project, slug=self.kwargs['slug'])
            messages.error(self.request, form['url'].errors[0])
            return redirect(target_obj.get_edit_url())


class DeleteBZUlrView(ProtectedDeleteView):
    model = BugzillaURL
    success_url = '/'

    def __init__(self):
        # remove GET from allowed methods to throw 405
        # copy list or modify global copy
        self.http_method_names = self.http_method_names[:]
        self.http_method_names.remove('get')
        super(DeleteBZUlrView, self).__init__()

    def delete(self, request, *args, **kwargs):
        if request.is_ajax():
            super(DeleteBZUlrView, self).delete(request, *args, **kwargs)
            return HttpResponse(status=204)
        return HttpResponseForbidden()


def server_error(request):
    context = {
        'STATIC_URL': getattr(settings, 'STATIC_URL', '/static/'),
        'ENABLE_GA': getattr(settings, 'ENABLE_GA', False),
        'request': {'path': '/'},
    }
    return render(request, '500.html', context_instance=Context(context),
                  status=500)


class RedirectOldURLsView(View):
    def get(self, request, *args, **kwargs):
        path = kwargs.get('path', '')
        return HttpResponsePermanentRedirect('/p/' + path)
