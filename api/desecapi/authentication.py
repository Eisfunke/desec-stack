import base64

from django.contrib.auth.hashers import PBKDF2PasswordHasher
from rest_framework import exceptions, HTTP_HEADER_ENCODING
from rest_framework.authentication import (
    BaseAuthentication,
    get_authorization_header,
    TokenAuthentication as RestFrameworkTokenAuthentication,
    BasicAuthentication)

from desecapi.models import Token
from desecapi.serializers import EmailPasswordSerializer


class TokenAuthentication(RestFrameworkTokenAuthentication):
    model = Token

    def authenticate_credentials(self, key):
        key = Token.make_hash(key)
        return super().authenticate_credentials(key)


class BasicTokenAuthentication(BaseAuthentication):
    """
    HTTP Basic authentication that uses username and token.

    Clients should authenticate by passing the username and the token as a
    password in the "Authorization" HTTP header, according to the HTTP
    Basic Authentication Scheme

        Authorization: Basic dXNlcm5hbWU6dG9rZW4=

    For username "username" and password "token".
    """

    # A custom token model may be used, but must have the following properties.
    #
    # * key -- The string identifying the token
    # * user -- The user to which the token belongs
    model = Token

    def authenticate(self, request):
        auth = get_authorization_header(request).split()

        if not auth or auth[0].lower() != b'basic':
            return None

        if len(auth) == 1:
            msg = 'Invalid basic auth token header. No credentials provided.'
            raise exceptions.AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = 'Invalid basic auth token header. Basic authentication string should not contain spaces.'
            raise exceptions.AuthenticationFailed(msg)

        return self.authenticate_credentials(auth[1])

    def authenticate_credentials(self, basic):
        invalid_token_message = 'Invalid basic auth token'
        try:
            user, key = base64.b64decode(basic).decode(HTTP_HEADER_ENCODING).split(':')
            key = Token.make_hash(key)
            token = self.model.objects.get(key=key)
            domain_names = token.user.domains.values_list('name', flat=True)
            if user not in ['', token.user.email] and not user.lower() in domain_names:
                raise Exception
        except Exception:
            raise exceptions.AuthenticationFailed(invalid_token_message)

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(invalid_token_message)

        return token.user, token

    def authenticate_header(self, request):
        return 'Basic'


class URLParamAuthentication(BaseAuthentication):
    """
    Authentication against username/password as provided in URL parameters.
    """
    model = Token

    def authenticate(self, request):
        """
        Returns a `User` if a correct username and password have been supplied
        using URL parameters.  Otherwise returns `None`.
        """

        if 'username' not in request.query_params:
            msg = 'No username URL parameter provided.'
            raise exceptions.AuthenticationFailed(msg)
        if 'password' not in request.query_params:
            msg = 'No password URL parameter provided.'
            raise exceptions.AuthenticationFailed(msg)

        return self.authenticate_credentials(request.query_params['username'], request.query_params['password'])

    def authenticate_credentials(self, _, key):
        key = Token.make_hash(key)
        try:
            token = self.model.objects.get(key=key)
        except self.model.DoesNotExist:
            raise exceptions.AuthenticationFailed('badauth')

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed('badauth')

        return token.user, token


class EmailPasswordPayloadAuthentication(BaseAuthentication):
    authenticate_credentials = BasicAuthentication.authenticate_credentials

    def authenticate(self, request):
        serializer = EmailPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self.authenticate_credentials(serializer.data['email'], serializer.data['password'], request)


class AuthenticatedActionAuthentication(BaseAuthentication):
    """
    Authenticates a request based on whether the serializer determines the validity of the given verification code
    and additional data (using `serializer.is_valid()`). The serializer's input data will be determined by (a) the
    view's 'code' kwarg and (b) the request payload for POST requests.

    If the request is valid, the AuthenticatedAction instance will be attached to the request as `auth` attribute.
    """
    def authenticate(self, request):
        view = request.parser_context['view']
        data = {**request.data, 'code': view.kwargs['code']}  # order crucial to avoid override from payload!
        serializer = view.serializer_class(data=data, context=view.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        try:
            action = serializer.Meta.model(**serializer.validated_data)
        except ValueError:
            exc = getattr(view, 'authentication_exception', exceptions.AuthenticationFailed)
            raise exc('Invalid code.')

        return action.user, action


class TokenHasher(PBKDF2PasswordHasher):
    algorithm = 'pbkdf2_sha256_iter1'
    iterations = 1
